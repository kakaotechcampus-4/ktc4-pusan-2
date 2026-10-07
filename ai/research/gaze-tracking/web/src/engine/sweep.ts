/**
 * Head circle check (port of `vision/runtime/sweep.py`; parity-tested against it).
 *
 * Between the set-up check and the calibration cues the presenter looks
 * straight ahead for a moment -- the centre of the circle -- then turns the head
 * slowly around.  A ring of ticks lights up in each direction the head reached
 * while the face stayed tracked.  It checks the tracking in every direction and
 * that the direction reading is the presenter's own; it changes nothing in the
 * classifier and never blocks on its own.
 *
 *     offset = (right, up) = (-(yaw - yaw0), pitch - pitch0)      degrees from the centre pose
 *     reach  = hypot(right / reach_yaw_deg, up / reach_pitch_deg)  1.0 = far enough
 *     angle  = atan2(up / reach_pitch_deg, right / reach_yaw_deg)  0 = presenter's right, CCW
 *
 * Tick 0 is centred on RIGHT and indices run counter-clockwise, like
 * `GAZE_DIRECTIONS`; with 32 ticks each direction owns 4.  Two turned frames in
 * a row also fill the ticks between them (8 FPS skips ticks otherwise); a frame
 * that moved faster than `max_speed_deg_s` lights nothing.
 */
import type { SweepConfig } from './config';
import { median } from './math';
import { GAZE_DIRECTIONS, toDeg, type GazeDirection, type Observation } from './types';

export type SweepState = 'IDLE' | 'CENTERING' | 'SWEEPING' | 'DONE' | 'TIMED_OUT';
export const TOO_FAST = 'TOO_FAST';
export const NO_HEAD_POSE = 'NO_HEAD_POSE';
export const NO_FACE = 'NO_FACE';

/** Below this reach the head is "at the centre": no pointer, no direction blamed for a loss. */
const POINTER_MIN_REACH = 0.35;

/** Python's `%` for a positive divisor (always non-negative). */
const mod = (a: number, n: number): number => ((a % n) + n) % n;

/** The 45-degree sector of an angle (0 = presenter's right, counter-clockwise). */
export function directionOfAngle(angleDeg: number): GazeDirection {
  return GAZE_DIRECTIONS[mod(Math.floor((angleDeg + 22.5) / 45), 8)]!;
}

/** Where the ring stands after a frame (Python `SweepStatus.to_dict()`). */
export interface SweepStatus {
  state: SweepState;
  ticks: boolean[];
  filled: number;
  total: number;
  progress: number;
  elapsed_ms: number;
  /** Centre pose `[yaw, pitch]` in degrees, once measured. */
  neutral_deg: [number, number] | null;
  /** Noise of that centre `[yaw, pitch]`: 1.4826 x MAD of its frames, degrees. */
  neutral_sigma_deg: [number, number] | null;
  /** This frame's head `[right, up]` in degrees from the centre (null without a pose). */
  offset_deg: [number, number] | null;
  /** This frame's turn on the reach ellipse (1 = far enough to light a tick). */
  reach: number;
  /** Angle the head points at (0 = presenter's right, CCW), when it is off the centre. */
  pointer_deg: number | null;
  /** Why this frame lit nothing (NO_FACE, TOO_FAST, ...). */
  last_reason: string | null;
  /** Direction of the largest gap still to fill, after `hint_after_ms` without progress. */
  hint: GazeDirection | null;
  /** Directions that still have unlit ticks, in `GAZE_DIRECTIONS` order. */
  missing: GazeDirection[];
  /** Times the face was lost while turned toward each direction. */
  lost: Partial<Record<GazeDirection, number>>;
  /** Largest head turn (degrees from the centre) reached per direction with the face tracked. */
  reached_deg: Partial<Record<GazeDirection, number>>;
  /** How far the head has turned toward each tick, 0..1 (1 = lit). */
  tick_reach: number[];
  /** Per direction, the mean `tick_reach` of its ticks: a gauge per direction. */
  direction_progress: Record<GazeDirection, number>;
  finished: boolean;
}

interface Turn {
  tMs: number;
  offset: [number, number];
  angle: number | null;
  reach: number;
  /** Not a jerk: the next frame may fill the ticks in between. */
  bridge: boolean;
}

export class HeadSweep {
  readonly cfg: SweepConfig;
  readonly #n: number;
  readonly #tickDeg: number;
  #state: SweepState = 'IDLE';
  #started = 0;
  #ticks: boolean[] = [];
  #reach: number[] = [];
  #centre: [number, number][] = [];
  #neutral: [number, number] | null = null;
  #neutralSigma: [number, number] | null = null;
  #prev: Turn | null = null;
  #lastFill = 0;
  #lost: Partial<Record<GazeDirection, number>> = {};
  #reached: Partial<Record<GazeDirection, number>> = {};
  #status: SweepStatus;

  constructor(cfg: SweepConfig) {
    if (!(cfg.ticks > 0) || cfg.ticks % 8)
      throw new Error(`sweep.ticks must be a positive multiple of 8, got ${cfg.ticks}`);
    this.cfg = cfg;
    this.#n = cfg.ticks;
    this.#tickDeg = 360 / this.#n;
    this.#status = this.#reset('IDLE', 0);
  }

  start(tMs: number): SweepStatus {
    return this.#reset('CENTERING', tMs);
  }

  get status(): SweepStatus {
    return this.#status;
  }

  tickDirection(index: number): GazeDirection {
    return directionOfAngle(index * this.#tickDeg);
  }

  offer(obs: Observation, tMs: number): SweepStatus {
    if (this.#state !== 'CENTERING' && this.#state !== 'SWEEPING') return this.#status;
    const t = Math.trunc(tMs);
    const pose = headDeg(obs);
    if (!pose) {
      const reason = obs.faceValid ? NO_HEAD_POSE : (obs.invalidReason ?? NO_FACE);
      this.#loseFace();
      return this.#finish(t, null, 0, null, reason);
    }

    if (this.#state === 'CENTERING') {
      this.#centre.push(pose);
      if (this.#centre.length < this.cfg.neutral_frames)
        return this.#finish(t, null, 0, null, null);
      this.#neutral = [
        median(this.#centre.map((p) => p[0])),
        median(this.#centre.map((p) => p[1])),
      ];
      const [ny, np] = this.#neutral;
      this.#neutralSigma = [
        1.4826 * median(this.#centre.map((p) => Math.abs(p[0] - ny))),
        1.4826 * median(this.#centre.map((p) => Math.abs(p[1] - np))),
      ];
      this.#state = 'SWEEPING';
      this.#lastFill = t;
    }

    const neutral = this.#neutral!;
    const offset: [number, number] = [-(pose[0] - neutral[0]), pose[1] - neutral[1]];
    const nx = offset[0] / this.cfg.reach_yaw_deg;
    const ny = offset[1] / this.cfg.reach_pitch_deg;
    const reach = Math.hypot(nx, ny);
    const angle = reach >= POINTER_MIN_REACH ? mod(toDeg(Math.atan2(ny, nx)), 360) : null;

    const prev = this.#prev;
    if (prev && t > prev.tMs) {
      const speed =
        (Math.hypot(offset[0] - prev.offset[0], offset[1] - prev.offset[1]) * 1000) /
        (t - prev.tMs);
      if (speed > this.cfg.max_speed_deg_s) {
        this.#prev = { tMs: t, offset, angle, reach, bridge: false };
        return this.#finish(t, offset, reach, angle, TOO_FAST);
      }
    }

    let lit = false;
    if (angle !== null) {
      this.#raise(this.#tickOf(angle), reach);
      if (reach >= 1) {
        const direction = directionOfAngle(angle);
        const magnitude = Math.hypot(offset[0], offset[1]);
        if (magnitude > (this.#reached[direction] ?? 0)) this.#reached[direction] = magnitude;
        lit = this.#light(this.#tickOf(angle));
      }
      if (
        prev &&
        prev.bridge &&
        prev.angle !== null &&
        t - prev.tMs > 0 &&
        t - prev.tMs <= this.cfg.max_gap_ms
      ) {
        const arc = mod(angle - prev.angle + 180, 360) - 180;
        if (Math.abs(arc) <= this.cfg.max_fill_arc_deg)
          lit =
            this.#fillBetween(
              this.#tickOf(prev.angle),
              this.#tickOf(angle),
              arc,
              Math.min(prev.reach, reach),
            ) || lit;
      }
    }
    if (lit) this.#lastFill = t;
    this.#prev = { tMs: t, offset, angle, reach, bridge: true };
    return this.#finish(t, offset, reach, angle, null);
  }

  // ------------------------------------------------------------ internals

  #reset(state: SweepState, tMs: number): SweepStatus {
    this.#state = state;
    this.#started = Math.trunc(tMs);
    this.#ticks = new Array<boolean>(this.#n).fill(false);
    this.#reach = new Array<number>(this.#n).fill(0);
    this.#centre = [];
    this.#neutral = null;
    this.#neutralSigma = null;
    this.#prev = null;
    this.#lastFill = Math.trunc(tMs);
    this.#lost = {};
    this.#reached = {};
    this.#status = this.#snapshot(Math.trunc(tMs), null, 0, null, null);
    return this.#status;
  }

  #tickOf(angle: number): number {
    return mod(Math.floor(mod(angle + this.#tickDeg / 2, 360) / this.#tickDeg), this.#n);
  }

  #raise(index: number, reach: number): void {
    this.#reach[index] = Math.max(this.#reach[index]!, Math.min(1, reach));
  }

  #light(index: number): boolean {
    if (this.#ticks[index]) return false;
    this.#ticks[index] = true;
    return true;
  }

  /** Ticks from `start` to `end` the short way: raised to `reach`, lit when it is 1. */
  #fillBetween(start: number, end: number, arc: number, reach: number): boolean {
    const step = arc > 0 ? 1 : -1;
    let lit = false;
    let index = start;
    for (let k = 0; k < this.#n; k++) {
      this.#raise(index, reach);
      if (reach >= 1) lit = this.#light(index) || lit;
      if (index === end) break;
      index = mod(index + step, this.#n);
    }
    return lit;
  }

  #loseFace(): void {
    const prev = this.#prev;
    if (prev && prev.angle !== null && this.#state === 'SWEEPING') {
      const direction = directionOfAngle(prev.angle);
      this.#lost[direction] = (this.#lost[direction] ?? 0) + 1;
    }
    this.#prev = null;
  }

  #finish(
    t: number,
    offset: [number, number] | null,
    reach: number,
    angle: number | null,
    reason: string | null,
  ): SweepStatus {
    if (this.#ticks.every(Boolean)) this.#state = 'DONE';
    else if (t - this.#started >= this.cfg.timeout_ms) this.#state = 'TIMED_OUT';
    this.#status = this.#snapshot(t, offset, reach, angle, reason);
    return this.#status;
  }

  #snapshot(
    t: number,
    offset: [number, number] | null,
    reach: number,
    angle: number | null,
    reason: string | null,
  ): SweepStatus {
    let hint: GazeDirection | null = null;
    if (this.#state === 'SWEEPING' && t - this.#lastFill >= this.cfg.hint_after_ms)
      hint = this.#largestGap();
    if (this.#state === 'TIMED_OUT') hint = this.#largestGap();
    const unlit = new Set(this.#ticks.flatMap((lit, i) => (lit ? [] : [this.tickDirection(i)])));
    const filled = this.#ticks.filter(Boolean).length;
    return {
      state: this.#state,
      ticks: [...this.#ticks],
      filled,
      total: this.#n,
      progress: this.#n ? filled / this.#n : 0,
      elapsed_ms: this.#state === 'IDLE' ? 0 : Math.max(0, t - this.#started),
      neutral_deg: this.#neutral ? [...this.#neutral] : null,
      neutral_sigma_deg: this.#neutralSigma ? [...this.#neutralSigma] : null,
      offset_deg: offset ? [...offset] : null,
      reach,
      pointer_deg: angle,
      last_reason: reason,
      hint,
      missing: GAZE_DIRECTIONS.filter((d) => unlit.has(d)),
      lost: { ...this.#lost },
      reached_deg: { ...this.#reached },
      tick_reach: [...this.#reach],
      direction_progress: Object.fromEntries(
        GAZE_DIRECTIONS.map((d) => {
          const own = this.#reach.filter((_, i) => this.tickDirection(i) === d);
          return [d, own.reduce((a, b) => a + b, 0) / own.length];
        }),
      ) as Record<GazeDirection, number>,
      finished: this.#state === 'DONE' || this.#state === 'TIMED_OUT',
    };
  }

  /** Direction at the middle of the longest run of unlit ticks (the ring wraps). */
  #largestGap(): GazeDirection | null {
    const n = this.#n;
    const ticks = this.#ticks;
    if (ticks.every(Boolean) || !ticks.some(Boolean)) return null;
    let bestStart = 0;
    let bestLen = 0;
    for (let start = 0; start < n; start++) {
      if (ticks[start] || !ticks[mod(start - 1, n)]) continue;
      let length = 0;
      while (length < n && !ticks[mod(start + length, n)]) length++;
      if (length > bestLen) {
        bestStart = start;
        bestLen = length;
      }
    }
    return directionOfAngle((bestStart + (bestLen - 1) / 2) * this.#tickDeg);
  }
}

function headDeg(obs: Observation): [number, number] | null {
  if (!obs.faceValid) return null;
  const { yaw, pitch, reprojectionError } = obs.headPose;
  // An unmeasured pose carries an infinite reprojection error.
  if (!Number.isFinite(yaw) || !Number.isFinite(pitch) || !Number.isFinite(reprojectionError))
    return null;
  return [toDeg(yaw), toDeg(pitch)];
}
