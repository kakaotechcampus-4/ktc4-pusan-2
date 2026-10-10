/**
 * Set-up check before calibration (port of `vision/runtime/preconditions.py`).
 *
 * PASS once every check has held for `hold_ms`; REJECT once one check has
 * failed for `reject_after_ms`; RETRY otherwise.  Frames that say nothing about
 * the set-up (a blink, a crop failure) neither advance nor break the hold.
 */
import type { PreconditionConfig } from './config';
import { focalLengthPx } from './headpose';
import { median } from './math';
import { toDeg, type HeadPose, type InvalidReason, type Observation } from './types';

export const IRIS_DIAMETER_CM = 1.17;

export type PreconditionStatus = 'PASS' | 'RETRY' | 'REJECT';
export type PreconditionReason =
  | 'OK'
  | 'NO_FACE'
  | 'MULTIPLE_FACES'
  | 'OFF_CENTER'
  | 'TOO_FAR'
  | 'TOO_CLOSE'
  | 'FACING_AWAY'
  | 'TOO_DARK'
  | 'BACKLIT'
  | 'LOW_FPS';

/** Report order and the tie-break for "which reason". */
export const CHECK_ORDER: readonly Exclude<PreconditionReason, 'OK'>[] = [
  'NO_FACE',
  'MULTIPLE_FACES',
  'OFF_CENTER',
  'TOO_FAR',
  'TOO_CLOSE',
  'FACING_AWAY',
  'TOO_DARK',
  'BACKLIT',
  'LOW_FPS',
];

export const PRECONDITION_HINTS: Record<PreconditionReason, string> = {
  OK: 'Hold still for a moment.',
  NO_FACE: 'Sit in front of the camera so your face is visible.',
  MULTIPLE_FACES: 'Only one person should be in the picture.',
  OFF_CENTER: 'Move so your face is in the middle of the picture.',
  TOO_FAR: 'Move closer to the camera.',
  TOO_CLOSE: 'Move back a little from the camera.',
  FACING_AWAY: 'Face the screen directly.',
  TOO_DARK: 'Your face is too dark. Turn on a light in front of you.',
  BACKLIT: 'There is strong light behind you. Close the blind or face the light.',
  LOW_FPS: 'The camera is too slow. Close other apps using the camera or CPU.',
};

const NEUTRAL_INVALID: ReadonlySet<InvalidReason> = new Set([
  'EYES_CLOSED',
  'LOW_FACE_CONFIDENCE',
  'CROP_FAILED',
  'BACKBONE_FAILED',
]);
const FPS_WINDOW = 8;

export interface CheckResult {
  name: Exclude<PreconditionReason, 'OK'>;
  ok: boolean;
  value: number | null;
}

export interface PreconditionReport {
  status: PreconditionStatus;
  reason: PreconditionReason;
  hint: string;
  blocking: boolean;
  held_ms: number;
  failing_ms: number;
  checks: CheckResult[];
  measurements: Record<string, number>;
}

export function irisDistanceCm(irisPx: number, imageHeight: number): number {
  if (!(irisPx > 0) || !(imageHeight > 0)) return NaN;
  return (focalLengthPx(imageHeight) * IRIS_DIAMETER_CM) / irisPx;
}

/**
 * Camera-to-head distance from the face-mesh fit, cm; NaN when unknown.  The
 * whole face model is fitted, so it needs no eye and stays put when the head
 * turns or the lids drop -- unlike the iris size.
 */
export function headDistanceCm(pose: HeadPose): number {
  const d = pose.depthCm;
  return Number.isFinite(d) && d > 0 ? d : NaN;
}

const r = (v: number, dp: number) => Math.round(v * 10 ** dp) / 10 ** dp;

export class PreconditionChecker {
  #holdSince: number | null = null;
  #failingSince = new Map<string, number>();
  #times: number[] = [];
  #last: PreconditionReport | null = null;
  #secondSince: number | null = null;
  private readonly cfg: PreconditionConfig;
  /** Adds the iris-size floor, which only an eye-reading backbone needs. */
  readonly eyeBased: boolean;

  constructor(cfg: PreconditionConfig, eyeBased = false) {
    this.cfg = cfg;
    this.eyeBased = eyeBased;
  }

  reset(): void {
    this.#holdSince = null;
    this.#secondSince = null;
    this.#failingSince.clear();
    this.#times = [];
    this.#last = null;
  }

  get lastReport(): PreconditionReport | null {
    return this.#last;
  }

  update(obs: Observation): PreconditionReport {
    const t = obs.tMs;
    if (this.#times.length && t < this.#times[this.#times.length - 1]!) this.#times = [];
    this.#times.push(t);
    if (this.#times.length > FPS_WINDOW + 1) this.#times.shift();

    if (!obs.faceValid && obs.invalidReason && NEUTRAL_INVALID.has(obs.invalidReason)) {
      const report = this.#report(t, this.#last ? this.#last.checks : [], {});
      this.#last = report;
      return report;
    }

    const [checks, measurements] = this.#evaluate(obs);
    const failing = checks.filter((c) => !c.ok).map((c) => c.name);
    if (failing.length) this.#holdSince = null;
    else if (this.#holdSince === null) this.#holdSince = t;
    for (const name of [...this.#failingSince.keys()])
      if (!failing.includes(name as never)) this.#failingSince.delete(name);
    for (const name of failing) if (!this.#failingSince.has(name)) this.#failingSince.set(name, t);

    const report = this.#report(t, checks, measurements);
    this.#last = report;
    return report;
  }

  #report(
    t: number,
    checks: CheckResult[],
    measurements: Record<string, number>,
  ): PreconditionReport {
    const cfg = this.cfg;
    if (this.#failingSince.size) {
      const order = new Map(CHECK_ORDER.map((n, i) => [n as string, i]));
      let name = '';
      let key: [number, number] | null = null;
      for (const [n, since] of this.#failingSince) {
        const k: [number, number] = [since, order.get(n) ?? 99];
        if (!key || k[0] < key[0] || (k[0] === key[0] && k[1] < key[1])) {
          name = n;
          key = k;
        }
      }
      const failingMs = t - this.#failingSince.get(name)!;
      const status: PreconditionStatus = failingMs >= cfg.reject_after_ms ? 'REJECT' : 'RETRY';
      const reason = name as PreconditionReason;
      return {
        status,
        reason,
        hint: PRECONDITION_HINTS[reason],
        blocking: status === 'REJECT' && cfg.strict,
        held_ms: 0,
        failing_ms: failingMs,
        checks,
        measurements,
      };
    }
    const held = this.#holdSince === null ? 0 : t - this.#holdSince;
    const status: PreconditionStatus =
      this.#holdSince !== null && held >= cfg.hold_ms ? 'PASS' : 'RETRY';
    return {
      status,
      reason: 'OK',
      hint: PRECONDITION_HINTS.OK,
      blocking: false,
      held_ms: held,
      failing_ms: 0,
      checks,
      measurements,
    };
  }

  #analysedFps(): number | null {
    if (this.#times.length < 4) return null;
    const gaps: number[] = [];
    for (let i = 1; i < this.#times.length; i++) {
      const g = this.#times[i]! - this.#times[i - 1]!;
      if (g > 0) gaps.push(g);
    }
    if (gaps.length === 0) return null;
    return 1000 / median(gaps);
  }

  #evaluate(obs: Observation): [CheckResult[], Record<string, number>] {
    const cfg = this.cfg;
    const checks: CheckResult[] = [];
    const m: Record<string, number> = {};
    const fps = this.#analysedFps();
    if (fps !== null) m.analysis_fps = r(fps, 2);
    const fpsCheck = (): CheckResult => ({
      name: 'LOW_FPS',
      ok: fps! >= cfg.min_analysis_fps,
      value: fps,
    });

    const hasFace = obs.invalidReason !== 'NO_FACE' && obs.faceBbox !== null;
    if (!hasFace) {
      checks.push({ name: 'NO_FACE', ok: false, value: null });
      if (fps !== null) checks.push(fpsCheck());
      return [checks, m];
    }
    checks.push({ name: 'NO_FACE', ok: true, value: null });

    const second = obs.scene.secondFaceAreaRatio;
    m.second_face_area_ratio = r(second, 3);
    // A second person only once seen for a while: one frame's false find is not one.
    let crowded = false;
    if (second > cfg.max_second_face_area_ratio) {
      if (this.#secondSince === null) this.#secondSince = obs.tMs;
      crowded = obs.tMs - this.#secondSince >= cfg.second_face_confirm_ms;
    } else this.#secondSince = null;
    checks.push({ name: 'MULTIPLE_FACES', ok: !crowded, value: second });

    const [width, height] = obs.imageSize;
    const [x, y, w, h] = obs.faceBbox!;
    let faceHeight = NaN;
    let faceArea = NaN;
    if (width > 0 && height > 0) {
      const dx = Math.abs((x + w / 2) / width - 0.5);
      const dy = Math.abs((y + h / 2) / height - 0.5);
      m.center_offset_x = r(dx, 3);
      m.center_offset_y = r(dy, 3);
      const off =
        dx > cfg.max_center_offset_x ||
        dy > cfg.max_center_offset_y ||
        obs.invalidReason === 'OUT_OF_FRAME';
      checks.push({ name: 'OFF_CENTER', ok: !off, value: Math.max(dx, dy) });
      faceHeight = h / height;
      faceArea = (w * h) / (width * height);
      m.face_height_ratio = r(faceHeight, 3);
      m.face_area_ratio = r(faceArea, 4);
    }

    // Distance only as information: the bounds are about whether the face can
    // be found and tracked, not about an ideal distance.  The face-mesh
    // distance needs no eye; the iris size is the fallback.
    const iris = obs.scene.irisDiameterPx;
    let distance = headDistanceCm(obs.headPose);
    if (!Number.isFinite(distance)) distance = irisDistanceCm(iris, height);
    m.iris_px = r(iris, 2);
    if (Number.isFinite(distance)) m.distance_cm = r(distance, 1);
    let tooFar =
      obs.invalidReason === 'FACE_TOO_SMALL' ||
      (Number.isFinite(faceArea) && faceArea < cfg.min_face_area_ratio);
    if (this.eyeBased) tooFar = tooFar || (iris > 0 && iris < cfg.min_iris_px);
    checks.push({
      name: 'TOO_FAR',
      ok: !tooFar,
      value: Number.isFinite(faceArea) ? faceArea : null,
    });
    const tooClose = Number.isFinite(faceHeight) && faceHeight > cfg.max_face_height_ratio;
    checks.push({
      name: 'TOO_CLOSE',
      ok: !tooClose,
      value: Number.isFinite(faceHeight) ? faceHeight : null,
    });

    const yaw = toDeg(obs.headPose.yaw);
    const pitch = toDeg(obs.headPose.pitch);
    m.head_yaw_deg = r(yaw, 1);
    m.head_pitch_deg = r(pitch, 1);
    const facing =
      Math.abs(yaw) <= cfg.max_head_yaw_deg && Math.abs(pitch) <= cfg.max_head_pitch_deg;
    checks.push({
      name: 'FACING_AWAY',
      ok: facing,
      value: Math.max(Math.abs(yaw), Math.abs(pitch)),
    });

    const brightness = obs.quality.faceBrightness;
    const backlight = obs.quality.backlightRatio;
    m.face_brightness = r(brightness, 1);
    m.backlight_ratio = r(backlight, 2);
    checks.push({ name: 'TOO_DARK', ok: brightness >= cfg.min_face_brightness, value: brightness });
    checks.push({ name: 'BACKLIT', ok: backlight <= cfg.max_backlight_ratio, value: backlight });

    if (fps !== null) checks.push(fpsCheck());
    return [checks, m];
  }
}
