/**
 * Live measurement conditions (port of `vision/runtime/condition.py`).
 *
 * Every live frame is compared with the calibration scene; each signal ramps
 * from 1.0 at its warn bound to `fail_reliability` at its fail bound and the
 * reliability is the minimum, so `issues` always names the culprit.
 *
 * Position drift: a move of the head is read as the head-angle error it causes
 * at a target -- `atan(move / distance)` sideways or up/down, the change of the
 * farthest cue's angle closer or further -- after taking out the part of the
 * face's motion in the picture that is only the head turning.  Distances come
 * from the face-mesh fit (no eye needed, unchanged by a turned or lowered head),
 * the iris size only when the frame or the calibration lacks it.  It fails at the
 * smallest separation between the calibrated cue postures (6-12 deg) and warns
 * at 60 % of that; beyond the fail bound for `drift_confirm_ms` (2 s) the
 * measurement is unusable (MOVED_TOO_FAR).  Lenient on purpose: presenters
 * shift in their seat.
 *
 * Reliability is "how likely is this judgement wrong or missing": position and
 * distance drift, head-direction jitter (shaky landmarks, whatever the cause),
 * face size against the detector floor, and frames that could not be judged.
 * A light change or a second person is a cause, not a signal: shaky landmarks
 * show as jitter, a takeover as FACE_REPLACED.  A distinct second face is a
 * *notice* that leaves reliability alone.
 *
 * The head-turn signal exists only for eye backbones (`headIsGaze = false`).
 * FACE_LOST / FACE_REPLACED / MOVED_TOO_FAR are severe: reliability 0.  A
 * replacement is a face that JUMPED between two sightings and stayed away from
 * the calibrated place -- never a gradual move; a second person counts once a
 * distinct face has been seen for `second_face_confirm_ms`.
 */
import type { ConditionConfig } from './config';
import { focalLengthPx } from './headpose';
import { median } from './math';
import { headDistanceCm } from './preconditions';
import type { Cue, Observation } from './types';
import { toDeg } from './types';

/** Adult iris diameter in centimetres (the same constant as the set-up check). */
const IRIS_CM = 1.17;
const DRIFT_CUES: readonly Cue[] = ['CAMERA', 'SCREEN', 'BOTTOM'];

export type ConditionIssue =
  | 'HEAD_TURNED'
  | 'TOO_FAR'
  | 'TOO_CLOSE'
  | 'OFF_CENTER'
  | 'SECOND_FACE'
  | 'LOW_VALID_RATIO'
  | 'NOISY_TRACKING'
  | 'FACE_LOST'
  | 'FACE_REPLACED'
  | 'MOVED_TOO_FAR';

/** Plain data so it can ride inside the stored calibration model. */
export interface SceneBaseline {
  centre: [number, number];
  faceArea: number;
  irisPx: number;
  brightness: number;
  head: [number, number];
  headPoses: Partial<Record<Cue, [number, number]>>;
  /** Camera-to-head distance from the face-mesh fit, cm (0 or absent when unknown). */
  depthCm?: number;
}

export function headDeviation(base: SceneBaseline, yawDeg: number, pitchDeg: number): number {
  const poses = Object.values(base.headPoses);
  const list = poses.length ? poses : [base.head];
  return Math.min(...list.map(([y, p]) => Math.max(Math.abs(yawDeg - y), Math.abs(pitchDeg - p))));
}

export function shiftedHeadPoses(
  base: SceneBaseline,
  newCamera: [number, number],
): Partial<Record<Cue, [number, number]>> {
  const camera = base.headPoses.CAMERA ?? base.head;
  const dy = newCamera[0] - camera[0];
  const dp = newCamera[1] - camera[1];
  const out: Partial<Record<Cue, [number, number]>> = {};
  for (const [cue, pose] of Object.entries(base.headPoses) as [Cue, [number, number]][])
    out[cue] = [pose[0] + dy, pose[1] + dp];
  return out;
}

function centreOf(obs: Observation): [number, number] | null {
  const b = obs.faceBbox;
  const [w, h] = obs.imageSize;
  if (!b || b[2] <= 0 || b[3] <= 0 || w <= 0 || h <= 0) return null;
  return [(b[0] + b[2] / 2) / w, (b[1] + b[3] / 2) / h];
}

function areaOf(obs: Observation): number | null {
  const b = obs.faceBbox;
  const [w, h] = obs.imageSize;
  if (!b || b[2] <= 0 || b[3] <= 0 || w <= 0 || h <= 0) return null;
  return (b[2] * b[3]) / (w * h);
}

export class SceneBaselineAccumulator {
  #rows: number[][] = [];
  #cues: (Cue | null)[] = [];

  reset(): void {
    this.#rows = [];
    this.#cues = [];
  }

  get size(): number {
    return this.#rows.length;
  }

  add(obs: Observation, cue: Cue | null = null): void {
    const c = centreOf(obs);
    const a = areaOf(obs);
    if (!c || a === null) return;
    this.#rows.push([
      c[0],
      c[1],
      a,
      obs.scene.irisDiameterPx,
      obs.quality.faceBrightness,
      toDeg(obs.headPose.yaw),
      toDeg(obs.headPose.pitch),
      headDistanceCm(obs.headPose),
    ]);
    this.#cues.push(cue);
  }

  build(): SceneBaseline | null {
    if (!this.#rows.length) return null;
    const col = (k: number) => median(this.#rows.map((r) => r[k]!).filter(Number.isFinite));
    const iris = this.#rows.map((r) => r[3]!).filter((v) => v > 0);
    const depth = this.#rows.map((r) => r[7]!).filter((v) => Number.isFinite(v) && v > 0);
    const headPoses: Partial<Record<Cue, [number, number]>> = {};
    const cues = [...new Set(this.#cues.filter((c): c is Cue => c !== null))].sort();
    for (const cue of cues) {
      const rows = this.#rows.filter(
        (_, i) =>
          this.#cues[i] === cue &&
          Number.isFinite(this.#rows[i]![5]!) &&
          Number.isFinite(this.#rows[i]![6]!),
      );
      if (rows.length)
        headPoses[cue] = [median(rows.map((r) => r[5]!)), median(rows.map((r) => r[6]!))];
    }
    return {
      centre: [col(0), col(1)],
      faceArea: col(2),
      irisPx: iris.length ? median(iris) : 0,
      brightness: col(4),
      head: [col(5), col(6)],
      headPoses,
      depthCm: depth.length ? median(depth) : 0,
    };
  }
}

/** How far the head moved from where it was calibrated, and what that costs. */
export interface Drift {
  /** The move, presenter-centric: their own right and up; `closer_cm` > 0 is closer. */
  right_cm: number;
  up_cm: number;
  closer_cm: number;
  distance_cm: number;
  calibrated_distance_cm: number;
  /** Head-angle error from the move sideways/up-down and from the distance change. */
  position_deg: number;
  distance_deg: number;
  deg: number;
  warn_deg: number;
  fail_deg: number;
}

export interface ConditionState {
  t_ms: number;
  reliability: number;
  issues: ConditionIssue[];
  severe: boolean;
  components: Record<string, number>;
  /** How far the head moved from the calibration position (null when unknown). */
  drift: Drift | null;
  /** Worth knowing but not lowering reliability (a distinct second face). */
  notices: ConditionIssue[];
  /** Head-direction noise over the recent window, deg (null when unknown). */
  jitter_deg: number | null;
}

/** `[warnDeg, failDeg, spanDeg]` for this calibration (see the module notes). */
/** Smallest distance between two calibrated cue postures, deg (null with fewer than two). */
function minCueSeparation(base: SceneBaseline): number | null {
  const poses = DRIFT_CUES.filter((c) => base.headPoses[c]).map((c) => base.headPoses[c]!);
  const seps: number[] = [];
  poses.forEach((a, i) =>
    poses.slice(i + 1).forEach((b) => seps.push(Math.hypot(a[0] - b[0], a[1] - b[1]))),
  );
  return seps.length ? Math.min(...seps) : null;
}

/** `[warnDeg, failDeg]` for the head-direction noise of this calibration (`jitter_limits`). */
export function jitterLimits(base: SceneBaseline, cfg: ConditionConfig): [number, number] {
  const sep = minCueSeparation(base);
  const fail =
    sep !== null
      ? Math.min(
          cfg.jitter_fail_max_deg,
          Math.max(cfg.jitter_fail_min_deg, cfg.jitter_fail_share * sep),
        )
      : cfg.jitter_fail_min_deg;
  return [cfg.jitter_warn_share * fail, fail];
}

/**
 * Frame-to-frame noise of the head angles, deg (`head_jitter_deg`): the robust
 * second difference cancels a steady turn; `1.4826 x median|d2| / sqrt(6)` per
 * axis, the larger axis; null with too few differences.
 */
export function headJitterDeg(
  samples: readonly [number, number, number][],
  maxGapMs: number,
  minSamples: number,
): number | null {
  const dy: number[] = [];
  const dp: number[] = [];
  for (let i = 2; i < samples.length; i++) {
    const [t0, y0, p0] = samples[i - 2]!;
    const [t1, y1, p1] = samples[i - 1]!;
    const [t2, y2, p2] = samples[i]!;
    if (t1 - t0 > maxGapMs || t2 - t1 > maxGapMs) continue;
    dy.push(Math.abs(y2 - 2 * y1 + y0));
    dp.push(Math.abs(p2 - 2 * p1 + p0));
  }
  if (dy.length < minSamples) return null;
  return Math.max(median(dy), median(dp)) * (1.4826 / Math.sqrt(6));
}

export function driftLimits(base: SceneBaseline, cfg: ConditionConfig): [number, number, number] {
  const poses = DRIFT_CUES.filter((c) => base.headPoses[c]).map(
    (c) => [c, base.headPoses[c]!] as const,
  );
  const sep = minCueSeparation(base);
  const fail =
    sep !== null
      ? Math.min(
          cfg.drift_fail_max_deg,
          Math.max(cfg.drift_fail_min_deg, cfg.drift_fail_share * sep),
        )
      : cfg.drift_fail_min_deg;
  const screen = base.headPoses.SCREEN;
  const others = poses.filter(([c]) => c !== 'SCREEN').map(([, p]) => p);
  let span: number;
  if (screen && others.length)
    span = Math.max(...others.map((p) => Math.hypot(p[0] - screen[0], p[1] - screen[1])));
  else if (base.headPoses.CAMERA && base.headPoses.BOTTOM) {
    const [c, b] = [base.headPoses.CAMERA, base.headPoses.BOTTOM];
    span = Math.hypot(c[0] - b[0], c[1] - b[1]) / 2;
  } else span = cfg.drift_default_span_deg;
  return [cfg.drift_warn_share * fail, fail, span];
}

/**
 * `[now, at calibration]` camera distance in cm, both from one source, or null:
 * the face-mesh distance when this frame and the calibration both have it, else
 * the iris size -- never one of each, which would read the gap between the two
 * estimates as a move.
 */
function distances(obs: Observation, base: SceneBaseline): [number, number] | null {
  const now = headDistanceCm(obs.headPose);
  const cal = base.depthCm ?? 0;
  if (Number.isFinite(now) && cal > 0) return [now, cal];
  const iris = obs.scene.irisDiameterPx;
  if (!(iris > 0 && base.irisPx > 0)) return null;
  const focal = focalLengthPx(obs.imageSize[1]);
  return [(focal * IRIS_CM) / iris, (focal * IRIS_CM) / base.irisPx];
}

/** The head's move from the calibration position (null without a distance or a face). */
export function measureDrift(
  obs: Observation,
  base: SceneBaseline,
  cfg: ConditionConfig,
  limits: [number, number, number] = driftLimits(base, cfg),
): Drift | null {
  const centre = centreOf(obs);
  const d = centre ? distances(obs, base) : null;
  if (!centre || !d) return null;
  const [warn, fail, span] = limits;
  const [width, height] = obs.imageSize;
  const [dNow, dCal] = d;
  const cmPerPx = dNow / focalLengthPx(height);
  let dx = (centre[0] - base.centre[0]) * width * cmPerPx; // image right +
  let dy = (centre[1] - base.centre[1]) * height * cmPerPx; // image down +
  const { yaw, pitch } = obs.headPose;
  const yaw0 = (base.head[0] * Math.PI) / 180;
  const pitch0 = (base.head[1] * Math.PI) / 180;
  if ([yaw, pitch, yaw0, pitch0].every(Number.isFinite)) {
    // A turned head moves the face in the picture while the head itself stays put.
    const r = cfg.head_radius_cm;
    dx -= r * (Math.sin(yaw) * Math.cos(pitch) - Math.sin(yaw0) * Math.cos(pitch0));
    dy += r * (Math.sin(pitch) - Math.sin(pitch0));
  }
  const position = toDeg(Math.atan2(Math.hypot(dx, dy), dNow));
  const spanRad = (span * Math.PI) / 180;
  const distance = Math.abs(toDeg(Math.atan((Math.tan(spanRad) * dCal) / dNow)) - span);
  return {
    right_cm: -dx,
    up_cm: -dy,
    closer_cm: dCal - dNow,
    distance_cm: dNow,
    calibrated_distance_cm: dCal,
    position_deg: position,
    distance_deg: distance,
    deg: Math.max(position, distance),
    warn_deg: warn,
    fail_deg: fail,
  };
}

function ramp(value: number, warn: number, fail: number, floor: number): number {
  if (!Number.isFinite(value) || value <= warn) return 1;
  if (value >= fail) return floor;
  return 1 - ((value - warn) / (fail - warn)) * (1 - floor);
}

export class ConditionMonitor {
  #valid: [number, boolean][] = [];
  #lastFaceMs: number | null = null;
  #replacedSince: number | null = null;
  #driftedSince: number | null = null;
  #secondSince: number | null = null;
  /** `[tMs, centre, area]` of the last frame with a face. */
  #prevFace: [number, [number, number], number] | null = null;
  #limits: [number, number, number];
  #jitterLimits: [number, number];
  /** `[tMs, yawDeg, pitchDeg]` of recent judged frames, for the jitter signal. */
  #heads: [number, number, number][] = [];
  #last: ConditionState | null = null;
  #lastEmitted: ConditionState | null = null;
  private readonly cfg: ConditionConfig;
  private baseline: SceneBaseline;
  /** Head-pose backbone: a turned head is a gaze direction, not a condition. */
  readonly headIsGaze: boolean;

  constructor(cfg: ConditionConfig, baseline: SceneBaseline, headIsGaze = false) {
    this.cfg = cfg;
    this.baseline = baseline;
    this.headIsGaze = headIsGaze;
    this.#limits = driftLimits(baseline, cfg);
    this.#jitterLimits = jitterLimits(baseline, cfg);
  }

  get scene(): SceneBaseline {
    return this.baseline;
  }

  get last(): ConditionState | null {
    return this.#last;
  }

  rebaseline(baseline: SceneBaseline): void {
    this.baseline = baseline;
    this.#limits = driftLimits(baseline, this.cfg);
    this.#jitterLimits = jitterLimits(baseline, this.cfg);
    this.#heads = [];
    this.#replacedSince = null;
    this.#driftedSince = null;
    this.#secondSince = null;
    this.#prevFace = null;
    this.#lastEmitted = null;
  }

  update(obs: Observation): ConditionState {
    const cfg = this.cfg;
    const base = this.baseline;
    const t = obs.tMs;
    const floor = cfg.fail_reliability;
    const components: Record<string, number> = {};
    const issues: ConditionIssue[] = [];

    this.#valid.push([t, obs.faceValid]);
    while (this.#valid.length && (t - this.#valid[0]![0] > cfg.window_ms || this.#valid[0]![0] > t))
      this.#valid.shift();
    const ratio = this.#valid.filter(([, v]) => v).length / this.#valid.length;
    components.valid = ramp(1 - ratio, 1 - cfg.valid_warn_ratio, 1 - cfg.valid_fail_ratio, floor);
    if (ratio < cfg.valid_warn_ratio) issues.push('LOW_VALID_RATIO');

    const centre = centreOf(obs);
    const hasFace = centre !== null && obs.invalidReason !== 'NO_FACE';
    if (this.#lastFaceMs === null || t < this.#lastFaceMs) this.#lastFaceMs = t;
    if (hasFace) this.#lastFaceMs = t;
    const severe: ConditionIssue[] = [];
    if (!hasFace && t - this.#lastFaceMs >= cfg.face_lost_ms) severe.push('FACE_LOST');

    if (hasFace && !this.headIsGaze) {
      const headDev = headDeviation(base, toDeg(obs.headPose.yaw), toDeg(obs.headPose.pitch));
      components.head = ramp(headDev, cfg.head_warn_deg, cfg.head_fail_deg, floor);
      if (headDev > cfg.head_warn_deg) issues.push('HEAD_TURNED');
    }

    // Head-direction noise over the recent window.
    const yawDeg = toDeg(obs.headPose.yaw);
    const pitchDeg = toDeg(obs.headPose.pitch);
    if (this.#heads.length && t < this.#heads[this.#heads.length - 1]![0]) this.#heads = [];
    if (
      obs.faceValid &&
      Number.isFinite(yawDeg) &&
      Number.isFinite(pitchDeg) &&
      Number.isFinite(obs.headPose.reprojectionError)
    )
      this.#heads.push([t, yawDeg, pitchDeg]);
    while (this.#heads.length && t - this.#heads[0]![0] > cfg.jitter_window_ms) this.#heads.shift();
    const jitter = headJitterDeg(this.#heads, cfg.jitter_max_gap_ms, cfg.jitter_min_samples);
    if (jitter !== null) {
      const [warn, fail] = this.#jitterLimits;
      components.jitter = ramp(jitter, warn, fail, floor);
      if (jitter > warn) issues.push('NOISY_TRACKING');
    }

    const notices: ConditionIssue[] = [];
    let drift: Drift | null = null;
    if (hasFace) {
      drift = measureDrift(obs, base, cfg, this.#limits);
      if (drift) {
        const { warn_deg: warn, fail_deg: fail } = drift;
        components.position = ramp(drift.position_deg, warn, fail, floor);
        components.distance = ramp(drift.distance_deg, warn, fail, floor);
        if (drift.position_deg > warn) issues.push('OFF_CENTER');
        if (drift.distance_deg > warn) issues.push(drift.closer_cm > 0 ? 'TOO_CLOSE' : 'TOO_FAR');
        if (drift.deg >= fail) {
          if (this.#driftedSince === null) this.#driftedSince = t;
          if (t - this.#driftedSince >= cfg.drift_confirm_ms) severe.push('MOVED_TOO_FAR');
        } else this.#driftedSince = null;
      }

      const shift = Math.max(
        Math.abs(centre[0] - base.centre[0]),
        Math.abs(centre[1] - base.centre[1]),
      );

      // The face against the recognition floor: too small to find, or so big it leaves the frame.
      const area = areaOf(obs);
      if (area !== null) {
        const heightRatio = obs.faceBbox![3] / obs.imageSize[1];
        const small = ramp(
          cfg.small_face_warn_area - area,
          0,
          cfg.small_face_warn_area - cfg.small_face_fail_area,
          floor,
        );
        const large = ramp(
          heightRatio,
          cfg.large_face_warn_height,
          cfg.large_face_fail_height,
          floor,
        );
        components.size = Math.min(small, large);
        if (area < cfg.small_face_warn_area && !issues.includes('TOO_FAR')) issues.push('TOO_FAR');
        if (heightRatio > cfg.large_face_warn_height && !issues.includes('TOO_CLOSE'))
          issues.push('TOO_CLOSE');
      }

      if (obs.scene.secondFaceAreaRatio >= cfg.second_face_area_ratio) {
        if (this.#secondSince === null) this.#secondSince = t;
        if (t - this.#secondSince >= cfg.second_face_confirm_ms) notices.push('SECOND_FACE');
      } else this.#secondSince = null;

      const faceArea = area ?? 0;
      const limit = cfg.replace_area_ratio;
      const changed = (offset: number, ratio: number) =>
        offset > cfg.replace_center_offset || !(1 / limit <= ratio && ratio <= limit);
      // The first sighting after calibration (or a re-anchor) is compared with the calibrated face.
      const prev = this.#prevFace ?? [t, base.centre, base.faceArea];
      if (t - prev[0] <= cfg.face_lost_ms) {
        const step = Math.max(Math.abs(centre[0] - prev[1][0]), Math.abs(centre[1] - prev[1][1]));
        // Another detection took over.
        if (changed(step, prev[2] > 0 ? faceArea / prev[2] : 1)) this.#replacedSince = t;
      }
      if (this.#replacedSince !== null) {
        if (!changed(shift, base.faceArea > 0 ? faceArea / base.faceArea : 1))
          this.#replacedSince = null; // back where the calibrated face was
        else if (t - this.#replacedSince >= cfg.replace_confirm_ms) severe.push('FACE_REPLACED');
      }
      this.#prevFace = [t, centre, faceArea];
    }

    const values = Object.values(components);
    const reliability = severe.length ? 0 : values.length ? Math.min(...values) : 1;
    const state: ConditionState = {
      t_ms: t,
      reliability,
      issues: [...severe, ...issues.filter((i) => !severe.includes(i))],
      severe: severe.length > 0,
      components,
      drift,
      notices,
      jitter_deg: jitter,
    };
    this.#last = state;
    return state;
  }

  /** First state, any change of issues or severity, a big reliability move, or the heartbeat. */
  shouldEmit(state: ConditionState): boolean {
    const prev = this.#lastEmitted;
    const a = new Set(prev?.issues ?? []);
    const b = new Set(state.issues);
    const sameIssues = prev !== null && a.size === b.size && [...a].every((i) => b.has(i));
    const due =
      prev === null ||
      state.severe !== prev.severe ||
      !sameIssues ||
      Math.abs(state.reliability - prev.reliability) >= this.cfg.emit_delta ||
      state.t_ms - prev.t_ms >= this.cfg.heartbeat_ms ||
      state.t_ms < prev.t_ms;
    if (due) this.#lastEmitted = state;
    return due;
  }
}
