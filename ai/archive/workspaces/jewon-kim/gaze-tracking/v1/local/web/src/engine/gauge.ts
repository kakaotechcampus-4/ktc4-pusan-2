/**
 * Good-frame gauge for one calibration cue (port of `vision/calibration/gauge.py`).
 *
 * A frame counts when the face is valid, the gaze is finite and confident, the
 * cue has settled, (eye backbones only) the head stayed put and it is not a
 * blink, (head-pose engine, with a reference pose) the head points the cue's
 * way from the reference -- up toward the lens, down toward the script, near
 * the centre for the screen -- and it is not a glance away from the cue's
 * running median.  Bad frames pause the gauge; they never reset it.
 */
import type { CalibrationConfig } from './config';
import { MAD_TO_SIGMA, median } from './math';
import type { Cue, InvalidReason, Observation } from './types';
import { toDeg } from './types';

export const LOW_GAZE_CONFIDENCE = 'LOW_GAZE_CONFIDENCE';
export const SETTLING = 'SETTLING';
export const HEAD_MOVED = 'HEAD_MOVED';
export const BLINK = 'BLINK';
export const OUTLIER = 'OUTLIER';
export const NOT_ACTIVE = 'NOT_ACTIVE';
/** The head did not go the cue's way from the reference pose (head-pose engine). */
export const LOOK_HIGHER = 'LOOK_HIGHER';
export const LOOK_LOWER = 'LOOK_LOWER';
export const OFF_TARGET = 'OFF_TARGET';

export type GaugeReason =
  | InvalidReason
  | typeof LOW_GAZE_CONFIDENCE
  | typeof SETTLING
  | typeof HEAD_MOVED
  | typeof BLINK
  | typeof OUTLIER
  | typeof NOT_ACTIVE
  | typeof LOOK_HIGHER
  | typeof LOOK_LOWER
  | typeof OFF_TARGET;

export type GaugeState = 'IDLE' | 'SETTLING' | 'COLLECTING' | 'DONE' | 'TIMED_OUT';

const NEUTRAL: ReadonlySet<string> = new Set([SETTLING, NOT_ACTIVE]);
const MIN_FRAMES_FOR_STATS = 4;

/** Gaze as the engine measured it for this frame, in radians. */
export interface GazeEstimate {
  yaw: number;
  pitch: number;
  confidence: number;
}

export interface GaugeStatus {
  cue: Cue | null;
  state: GaugeState;
  good: number;
  target: number;
  elapsed_ms: number;
  rejected: Record<string, number>;
  dominant_reason: string | null;
  last_reason: string | null;
  progress: number;
  finished: boolean;
}

/** Does a head turned `(right, up)` degrees from the reference point the cue's way? */
export function directionReason(
  cue: Cue | null,
  rightDeg: number,
  upDeg: number,
  cfg: CalibrationConfig,
): string | null {
  if (Math.abs(rightDeg) > cfg.cue_max_side_deg) return OFF_TARGET;
  if (cue === 'CAMERA' && upDeg < cfg.cue_min_up_deg) return LOOK_HIGHER;
  if (cue === 'BOTTOM' && upDeg > -cfg.cue_min_down_deg) return LOOK_LOWER;
  if (cue === 'SCREEN' && Math.hypot(rightDeg, upDeg) > cfg.cue_screen_radius_deg)
    return OFF_TARGET;
  return null;
}

export class CalibrationGauge {
  #headReference: [number, number] | null = null;
  #directionReference: [number, number] | null = null;
  #cue: Cue | null = null;
  #target: number;
  #state: GaugeState = 'IDLE';
  #startedMs = 0;
  #elapsedMs = 0;
  #gaze: [number, number][] = [];
  #ear: number[] = [];
  #rejected = new Map<string, number>();
  #lastReason: string | null = null;
  private readonly cfg: CalibrationConfig;
  private readonly blinkRatio: number;
  readonly eyeBased: boolean;

  constructor(cfg: CalibrationConfig, blinkRatio = 0.7, eyeBased = false) {
    this.cfg = cfg;
    this.#target = cfg.target_good_frames;
    this.blinkRatio = blinkRatio;
    this.eyeBased = eyeBased;
  }

  get headReference(): [number, number] | null {
    return this.#headReference;
  }

  /** Head `[yawDeg, pitchDeg]` the cue directions are read from (head-pose engine). */
  get directionReference(): [number, number] | null {
    return this.#directionReference;
  }

  setDirectionReference(reference: readonly [number, number] | null): void {
    this.#directionReference = reference ? [reference[0], reference[1]] : null;
  }

  /** Begin a cue (`target` good frames, default `target_good_frames`). */
  start(cue: Cue, tMs: number, target: number | null = null): void {
    this.#cue = cue;
    this.#target = target ?? this.cfg.target_good_frames;
    this.#state = 'SETTLING';
    this.#startedMs = tMs;
    this.#elapsedMs = 0;
    this.#gaze = [];
    this.#ear = [];
    this.#rejected = new Map();
    this.#lastReason = null;
  }

  get status(): GaugeStatus {
    let dominant: string | null = null;
    let best = -1;
    for (const [k, v] of this.#rejected) {
      if (NEUTRAL.has(k)) continue;
      if (v > best) {
        dominant = k;
        best = v;
      }
    }
    const target = this.#target;
    const good = this.#gaze.length;
    return {
      cue: this.#cue,
      state: this.#state,
      good,
      target,
      elapsed_ms: this.#elapsedMs,
      rejected: Object.fromEntries(this.#rejected),
      dominant_reason: dominant,
      last_reason: this.#lastReason,
      progress: target <= 0 ? 0 : Math.min(1, good / target),
      finished: this.#state === 'DONE' || this.#state === 'TIMED_OUT',
    };
  }

  /** Judge one frame: `[accepted, rejectReason]`. */
  offer(obs: Observation, gaze: GazeEstimate | null, tMs: number): [boolean, string | null] {
    if (this.#state !== 'SETTLING' && this.#state !== 'COLLECTING') return [false, NOT_ACTIVE];
    this.#elapsedMs = Math.max(0, tMs - this.#startedMs);
    if (this.#elapsedMs >= this.cfg.cue_timeout_ms) {
      this.#finishOnTimeout();
      return [false, NOT_ACTIVE];
    }
    const reason = this.#rejectReason(obs, gaze);
    this.#lastReason = reason;
    if (reason !== null) {
      this.#rejected.set(reason, (this.#rejected.get(reason) ?? 0) + 1);
      return [false, reason];
    }
    this.#gaze.push([toDeg(gaze!.yaw), toDeg(gaze!.pitch)]);
    this.#ear.push(obs.quality.minEyeOpenness);
    if (this.#gaze.length >= this.#target) this.#state = 'DONE';
    return [true, null];
  }

  /** Raise this cue's target and go on collecting (a done cue reopens; the timeout still runs). */
  extend(target: number): void {
    this.#target = Math.max(this.#target, target);
    if (this.#state === 'DONE' && this.#gaze.length < this.#target) this.#state = 'COLLECTING';
  }

  /** Advance the clock without a frame and report. */
  tick(tMs: number): GaugeStatus {
    if (this.#state === 'SETTLING' || this.#state === 'COLLECTING') {
      this.#elapsedMs = Math.max(0, tMs - this.#startedMs);
      if (this.#elapsedMs >= this.cfg.cue_timeout_ms) this.#finishOnTimeout();
    }
    return this.status;
  }

  #finishOnTimeout(): void {
    this.#state = this.#gaze.length >= this.cfg.min_samples_per_class ? 'DONE' : 'TIMED_OUT';
  }

  #rejectReason(obs: Observation, gaze: GazeEstimate | null): string | null {
    if (!obs.faceValid) return obs.invalidReason ?? 'NO_FACE';
    if (!gaze || !Number.isFinite(gaze.yaw) || !Number.isFinite(gaze.pitch))
      return 'BACKBONE_FAILED';
    if (!(gaze.confidence >= this.cfg.min_sample_confidence)) return LOW_GAZE_CONFIDENCE;
    if (this.#elapsedMs < this.cfg.cue_settle_ms) return SETTLING;
    this.#state = 'COLLECTING';

    const head: [number, number] = [toDeg(obs.headPose.yaw), toDeg(obs.headPose.pitch)];
    if (this.eyeBased && head.every(Number.isFinite)) {
      if (this.#headReference === null) this.#headReference = head;
      const dev = Math.max(
        Math.abs(head[0] - this.#headReference[0]),
        Math.abs(head[1] - this.#headReference[1]),
      );
      if (dev > this.cfg.max_head_deviation_deg) return HEAD_MOVED;
    }
    const ref = this.#directionReference;
    if (!this.eyeBased && ref && this.cfg.cue_direction_gate && head.every(Number.isFinite)) {
      // Presenter-centric: yaw > 0 turns toward the image right, the presenter's left.
      const reason = directionReason(this.#cue, -(head[0] - ref[0]), head[1] - ref[1], this.cfg);
      if (reason) return reason;
    }
    if (this.eyeBased && this.#ear.length >= MIN_FRAMES_FOR_STATS) {
      const ref = median(this.#ear);
      if (ref > 0 && obs.quality.minEyeOpenness < this.blinkRatio * ref) return BLINK;
    }
    if (this.#gaze.length >= MIN_FRAMES_FOR_STATS) {
      const g: [number, number] = [toDeg(gaze.yaw), toDeg(gaze.pitch)];
      for (const k of [0, 1] as const) {
        const col = this.#gaze.map((p) => p[k]);
        const centre = median(col);
        const spread = MAD_TO_SIGMA * median(col.map((v) => Math.abs(v - centre)));
        const scale = Math.max(spread, this.cfg.sigma_min_deg);
        if (Math.abs(g[k] - centre) > this.cfg.outlier_k * scale) return OUTLIER;
      }
    }
    return null;
  }
}
