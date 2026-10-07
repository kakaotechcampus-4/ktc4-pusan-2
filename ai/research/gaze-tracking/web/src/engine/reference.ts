/**
 * Reference-anchor classifier (port of `vision/calibration/references.py`).
 *
 * Each calibration cue leaves an anchor (median gaze, degrees); every class is
 * a region in gaze space blurred by the pooled measurement noise (a "soft
 * box"), OTHER is a constant floor, and the posterior comes from Bayes in log
 * space.  The model is plain data (`ReferenceModelData`) so it survives
 * `structuredClone` / IndexedDB, which the frontend needs to restore a
 * calibration in a later Take.
 */
import type { CalibrationConfig } from './config';
import { logNdtr, logsumexp, MAD_TO_SIGMA, median } from './math';
import {
  CUES,
  GAZE_DIRECTIONS,
  STATE_CLASSES,
  type Cue,
  type GazeDirection,
  type Sample,
  type State,
  type StateClass,
} from './types';

export const MODEL_SCHEMA = 'gaze-ref/1';

export const UNCERTAIN_LOW_CONFIDENCE = 'LOW_CONFIDENCE';
export const UNCERTAIN_LOW_MARGIN = 'LOW_MARGIN';
export const UNCERTAIN_HEAD_AWAY = 'HEAD_AWAY';
export const INVERTED_PITCH_HINT_PREFIX = 'INVERTED_PITCH:';

const MIN_SAMPLES_FOR_STATS = 2;
const SELF_POSTERIOR_MARGIN = 0.05;
const LOG_SQRT_2PI = 0.5 * Math.log(2 * Math.PI);

export type FailReason =
  | 'NOT_ENOUGH_SAMPLES'
  | 'DEGENERATE_FEATURES'
  | 'CLASS_NOT_SEPARABLE'
  | 'CENTROIDS_TOO_CLOSE'
  | 'ANCHOR_AMBIGUOUS'
  | 'LOW_LOO_ACCURACY';

const HINTS: Record<FailReason, string> = {
  NOT_ENOUGH_SAMPLES:
    'Not enough usable frames. Keep your whole face in view and hold each look until the gauge fills, then try again.',
  DEGENERATE_FEATURES:
    'The gaze never moved between the targets. Check the camera and the light, then try again looking clearly at each target.',
  CLASS_NOT_SEPARABLE:
    'Looking at the lens and looking at the script measured almost the same. Look straight into the lens first, then clearly down at the script.',
  CENTROIDS_TOO_CLOSE:
    'Two of the targets measured too close together. Look clearly at each one in turn.',
  ANCHOR_AMBIGUOUS:
    'One target could not be told apart from the region around it. Sit a little closer and look clearly at each target.',
  LOW_LOO_ACCURACY:
    'The samples of different targets overlap. Look clearly at each target and hold each look steady.',
};

export const SCREEN_MERGED_WARNING =
  'SCREEN_MERGED: looking at the lens and at the screen centre measured too close to tell apart, so a look at the screen counts as CAMERA (facing front) for this calibration.';

const SCREEN_FIXABLE: ReadonlySet<string> = new Set([
  'CENTROIDS_TOO_CLOSE',
  'ANCHOR_AMBIGUOUS',
  'LOW_LOO_ACCURACY',
]);

// --------------------------------------------------------------------------
// Densities
// --------------------------------------------------------------------------

function log1mexp(d: number): number {
  if (d >= 0) return -Infinity;
  if (d > -0.6931471805599453) return Math.log(-Math.expm1(d));
  return Math.log1p(-Math.exp(d));
}

function logNdtrDiff(a: number, b: number): number {
  if (a > 0) [a, b] = [-b, -a];
  const lb = logNdtr(b);
  const la = logNdtr(a);
  return lb + log1mexp(la - lb);
}

/** Log density at `x` of `Uniform[lo, hi]` convolved with `N(0, s^2)`. */
export function softBoxLogDensity(x: number, lo: number, hi: number, s: number): number {
  s = Math.max(s, 1e-9);
  if (lo > hi) [lo, hi] = [hi, lo];
  const width = hi - lo;
  if (width < 1e-3 * s) {
    const z = (x - 0.5 * (lo + hi)) / s;
    return -0.5 * z * z - Math.log(s) - LOG_SQRT_2PI;
  }
  return logNdtrDiff((lo - x) / s, (hi - x) / s) - Math.log(width);
}

/** One axis's noise level from residuals about each cue's own median. */
export function robustSigma(residuals: readonly number[], cfg: CalibrationConfig): number {
  const r = residuals.filter(Number.isFinite);
  const mad = r.length ? median(r.map(Math.abs)) : 0;
  return Math.max(cfg.sigma_min_deg, cfg.sigma_scale * MAD_TO_SIGMA * mad);
}

// --------------------------------------------------------------------------
// Model
// --------------------------------------------------------------------------

export interface SoftBox {
  yawLo: number;
  yawHi: number;
  pitchLo: number;
  pitchHi: number;
}

export interface ReferenceModelData {
  schema: typeof MODEL_SCHEMA;
  anchors: Partial<Record<Cue, [number, number]>>;
  counts: Record<Cue, number>;
  sigma: [number, number];
  boxes: Partial<Record<Cue, SoftBox>>;
  logPriors: Partial<Record<StateClass, number>>;
  logOtherDensity: number;
  headBaseline: [number, number];
  classes: StateClass[];
  spreads: Partial<Record<Cue, [number, number]>>;
}

const boxArea = (b: SoftBox): number => (b.yawHi - b.yawLo) * (b.pitchHi - b.pitchLo);

function cueValues(samples: readonly Sample[]): Record<Cue, [number, number][]> {
  const rows: Record<Cue, [number, number][]> = { CAMERA: [], SCREEN: [], BOTTOM: [] };
  for (const s of samples) {
    if (!(s.cue in rows)) continue;
    if (Number.isFinite(s.yawDeg) && Number.isFinite(s.pitchDeg))
      rows[s.cue].push([s.yawDeg, s.pitchDeg]);
  }
  return rows;
}

function headBaseline(samples: readonly Sample[]): [number, number] {
  const heads = samples.filter(
    (s) => Number.isFinite(s.headYawDeg) && Number.isFinite(s.headPitchDeg),
  );
  if (heads.length === 0) return [0, 0];
  return [median(heads.map((s) => s.headYawDeg)), median(heads.map((s) => s.headPitchDeg))];
}

/** The model from one calibration, or `null` without CAMERA and BOTTOM. */
export function buildReferenceModel(
  samples: readonly Sample[],
  cfg: CalibrationConfig,
): ReferenceModelData | null {
  const values = cueValues(samples);
  if (values.CAMERA.length < 1 || values.BOTTOM.length < 1) return null;
  const present = CUES.filter((c) => values[c].length >= 1);

  const anchors: Partial<Record<Cue, [number, number]>> = {};
  const spreads: Partial<Record<Cue, [number, number]>> = {};
  const resYaw: number[] = [];
  const resPitch: number[] = [];
  for (const cue of present) {
    const v = values[cue];
    const med: [number, number] = [median(v.map((p) => p[0])), median(v.map((p) => p[1]))];
    anchors[cue] = med;
    const dy = v.map((p) => p[0] - med[0]);
    const dp = v.map((p) => p[1] - med[1]);
    resYaw.push(...dy);
    resPitch.push(...dp);
    spreads[cue] = [
      MAD_TO_SIGMA * median(dy.map(Math.abs)),
      MAD_TO_SIGMA * median(dp.map(Math.abs)),
    ];
  }
  const sigma: [number, number] = [robustSigma(resYaw, cfg), robustSigma(resPitch, cfg)];

  const boxes: Partial<Record<Cue, SoftBox>> = {};
  const [cy, cp] = anchors.CAMERA!;
  const r = Math.max(0, cfg.camera_halfwidth_deg);
  boxes.CAMERA = { yawLo: cy - r, yawHi: cy + r, pitchLo: cp - r, pitchHi: cp + r };
  const [by, bp] = anchors.BOTTOM!;
  if (anchors.SCREEN) {
    const [sy, sp] = anchors.SCREEN;
    const aspect = Math.max(cfg.screen_aspect, 1e-6);
    const dYaw = Math.abs(sy - cy);
    const dPitch = Math.abs(sp - cp);
    const halfH = Math.max(dPitch, dYaw / aspect);
    const halfW = Math.max(dYaw, Math.min(dPitch * aspect, cfg.screen_max_halfwidth_deg));
    boxes.SCREEN = {
      yawLo: sy - halfW,
      yawHi: sy + halfW,
      pitchLo: sp - halfH,
      pitchHi: sp + halfH,
    };
    const sw = cfg.script_width_fraction * halfW;
    const sh = cfg.script_height_fraction * halfH;
    boxes.BOTTOM = { yawLo: by - sw, yawHi: by + sw, pitchLo: bp - sh, pitchHi: bp + sh };
  } else {
    boxes.BOTTOM = { yawLo: by, yawHi: by, pitchLo: bp, pitchHi: bp };
  }

  const priors: Record<StateClass, number> = {
    CAMERA: cfg.prior_camera,
    SCREEN: anchors.SCREEN ? cfg.prior_screen : 0,
    BOTTOM: cfg.prior_bottom,
    OTHER: cfg.prior_other,
  };
  if (priors.CAMERA <= 0 || priors.BOTTOM <= 0)
    throw new Error('prior_camera and prior_bottom must be > 0');
  const classes = STATE_CLASSES.filter((c) => priors[c] > 0);
  const total = classes.reduce((s, c) => s + priors[c], 0);
  const logPriors: Partial<Record<StateClass, number>> = {};
  for (const c of classes) logPriors[c] = Math.log(priors[c] / total);
  const fieldArea = Math.max(cfg.other_field_yaw_deg * cfg.other_field_pitch_deg, 1e-6);

  return {
    schema: MODEL_SCHEMA,
    anchors,
    counts: {
      CAMERA: values.CAMERA.length,
      SCREEN: values.SCREEN.length,
      BOTTOM: values.BOTTOM.length,
    },
    sigma,
    boxes,
    logPriors,
    logOtherDensity: -Math.log(fieldArea),
    headBaseline: headBaseline(samples),
    classes,
    spreads,
  };
}

export function logLikelihood(
  m: ReferenceModelData,
  cls: StateClass,
  yaw: number,
  pitch: number,
): number {
  if (cls === 'OTHER') return m.logOtherDensity;
  const b = m.boxes[cls]!;
  return (
    softBoxLogDensity(yaw, b.yawLo, b.yawHi, m.sigma[0]) +
    softBoxLogDensity(pitch, b.pitchLo, b.pitchHi, m.sigma[1])
  );
}

/**
 * Class posterior at one gaze (degrees), keyed in `classes` order.  OTHER takes
 * part only once the gaze is at least `otherMarginDeg` outside the calibrated
 * screen area (at least `minHalfwidthDeg` wide each side); closer in it is 0
 * and the nearest screen class wins.
 */
export function posterior(
  m: ReferenceModelData,
  yaw: number,
  pitch: number,
  otherMarginDeg = 0,
  minHalfwidthDeg = 0,
): Partial<Record<StateClass, number>> {
  let active = m.classes;
  if (otherMarginDeg > 0 && active.includes('OTHER')) {
    const [right, up] = gazeOffset(m, yaw, pitch, minHalfwidthDeg);
    if (Math.hypot(right, up) < otherMarginDeg) active = active.filter((c) => c !== 'OTHER');
  }
  const logs = active.map((c) => m.logPriors[c]! + logLikelihood(m, c, yaw, pitch));
  const norm = logsumexp(logs);
  const out: Partial<Record<StateClass, number>> = {};
  m.classes.forEach((c) => {
    const i = active.indexOf(c);
    out[c] = i >= 0 ? Math.exp(logs[i]! - norm) : 0;
  });
  return out;
}

/** Every anchor and region moved by one gaze offset (re-anchor). */
export function shiftedModel(
  m: ReferenceModelData,
  dYaw: number,
  dPitch: number,
): ReferenceModelData {
  const anchors: Partial<Record<Cue, [number, number]>> = {};
  const boxes: Partial<Record<Cue, SoftBox>> = {};
  for (const c of CUES) {
    const a = m.anchors[c];
    if (a) anchors[c] = [a[0] + dYaw, a[1] + dPitch];
    const b = m.boxes[c];
    if (b)
      boxes[c] = {
        yawLo: b.yawLo + dYaw,
        yawHi: b.yawHi + dYaw,
        pitchLo: b.pitchLo + dPitch,
        pitchHi: b.pitchHi + dPitch,
      };
  }
  return { ...m, anchors, boxes };
}

/** Radius within which a gaze still reads as CAMERA over its neighbour. */
export function cameraCaptureRadiusDeg(m: ReferenceModelData): number {
  const s = Math.sqrt(m.sigma[0] * m.sigma[1]);
  const logPeak = m.logPriors.CAMERA! - Math.log(2 * Math.PI * m.sigma[0] * m.sigma[1]);
  let logRival: number;
  if (m.boxes.SCREEN && m.logPriors.SCREEN !== undefined && boxArea(m.boxes.SCREEN) > 0) {
    logRival = m.logPriors.SCREEN - Math.log(boxArea(m.boxes.SCREEN));
  } else if (m.logPriors.OTHER !== undefined) {
    logRival = m.logPriors.OTHER + m.logOtherDensity;
  } else {
    return Infinity;
  }
  const ratio = logPeak - logRival;
  return ratio > 0 ? s * Math.sqrt(2 * ratio) : 0;
}

// --------------------------------------------------------------------------
// Decision rule
// --------------------------------------------------------------------------

/** doc 5-4 rule on a posterior: `[label, uncertainReason]`. */
export function decideLabel(
  probs: Partial<Record<StateClass, number>>,
  cfg: CalibrationConfig,
): [State, string | null] {
  const ranked = Object.values(probs).sort((a, b) => b - a);
  const pMax = ranked[0] ?? 0;
  const margin = ranked.length > 1 ? pMax - ranked[1]! : pMax;
  if (pMax < cfg.p_max_threshold) return ['UNCERTAIN', UNCERTAIN_LOW_CONFIDENCE];
  if (margin < cfg.margin_threshold) return ['UNCERTAIN', UNCERTAIN_LOW_MARGIN];
  let best: StateClass = STATE_CLASSES[0]!;
  let bestP = -1;
  for (const c of STATE_CLASSES) {
    const p = probs[c] ?? -1;
    if (p > bestP) {
      best = c;
      bestP = p;
    }
  }
  return [best, null];
}

/**
 * The calibrated screen area: every class region together, widened by one
 * noise unit so edge jitter does not read as a look "outside", and at least
 * `minHalfwidthDeg` wide each side of its middle (head-pose looks at the lens,
 * screen centre and script often line up within a degree or two sideways).
 */
export function screenRegion(m: ReferenceModelData, minHalfwidthDeg = 0): SoftBox {
  const boxes = CUES.map((c) => m.boxes[c]).filter((b): b is SoftBox => b !== undefined);
  let yawLo = Math.min(...boxes.map((b) => b.yawLo)) - m.sigma[0];
  let yawHi = Math.max(...boxes.map((b) => b.yawHi)) + m.sigma[0];
  if ((yawHi - yawLo) / 2 < minHalfwidthDeg) {
    const mid = (yawLo + yawHi) / 2;
    [yawLo, yawHi] = [mid - minHalfwidthDeg, mid + minHalfwidthDeg];
  }
  return {
    yawLo,
    yawHi,
    pitchLo: Math.min(...boxes.map((b) => b.pitchLo)) - m.sigma[1],
    pitchHi: Math.max(...boxes.map((b) => b.pitchHi)) + m.sigma[1],
  };
}

/**
 * `[rightDeg, upDeg]` the gaze lies outside the screen area; `[0, 0]` inside.
 * Presenter-centric: in the raw frame a gaze toward the image right (yaw > 0)
 * is toward the presenter's LEFT, so the horizontal sign flips.
 */
/** How far `v` lies outside `[lo, hi]` (signed), 0 inside. */
function beyond(v: number, lo: number, hi: number): number {
  if (v > hi) return v - hi;
  if (v < lo) return v - lo;
  return 0;
}

export function gazeOffset(
  m: ReferenceModelData,
  yawDeg: number,
  pitchDeg: number,
  minHalfwidthDeg = 0,
): [number, number] {
  const r = screenRegion(m, minHalfwidthDeg);
  const ex = beyond(yawDeg, r.yawLo, r.yawHi);
  const ey = beyond(pitchDeg, r.pitchLo, r.pitchHi);
  return [ex ? -ex : 0, ey];
}

/**
 * `[rightDeg, upDeg]` of the gaze from the screen-centre look, presenter-centric
 * (`aim_offset`).  Always defined, unlike `gazeOffset`: where the head points,
 * read against the calibration.  The reference is the SCREEN anchor, or halfway
 * between the lens and the script without one.
 */
export function aimOffset(
  m: ReferenceModelData,
  yawDeg: number,
  pitchDeg: number,
): [number, number] {
  let ref: [number, number];
  if (m.anchors.SCREEN) ref = m.anchors.SCREEN;
  else {
    const [cy, cp] = m.anchors.CAMERA!;
    const [by, bp] = m.anchors.BOTTOM!;
    ref = [(cy + by) / 2, (cp + bp) / 2];
  }
  return [-(yawDeg - ref[0]), pitchDeg - ref[1]];
}

/** The 45-degree sector of an outside offset, or `null` inside the screen area. */
export function directionOf(offset: readonly [number, number]): GazeDirection | null {
  const [right, up] = offset;
  if (right === 0 && up === 0) return null;
  const angle = (Math.atan2(up, right) * 180) / Math.PI;
  const index = (((Math.floor((angle + 22.5) / 45) % 8) + 8) % 8) as number;
  return GAZE_DIRECTIONS[index]!;
}

export function headIsAway(
  m: ReferenceModelData,
  headYawDeg: number,
  headPitchDeg: number,
  cfg: CalibrationConfig,
): boolean {
  if (!Number.isFinite(headYawDeg) || !Number.isFinite(headPitchDeg)) return false;
  return (
    Math.abs(headYawDeg - m.headBaseline[0]) > cfg.head_away_yaw_deg ||
    Math.abs(headPitchDeg - m.headBaseline[1]) > cfg.head_away_pitch_deg
  );
}

/**
 * One usable frame's decision (`ReferenceAnchorClassifier.decide` for a valid
 * frame): a head turned far from the calibration pose is OTHER whatever the
 * gaze reads, or UNCERTAIN/HEAD_AWAY when OTHER is switched off.
 */
export function decideFrame(
  m: ReferenceModelData,
  gazeYawDeg: number,
  gazePitchDeg: number,
  headYawDeg: number,
  headPitchDeg: number,
  cfg: CalibrationConfig,
): { state: State; probs: Partial<Record<StateClass, number>>; reason: string | null } {
  const away = headIsAway(m, headYawDeg, headPitchDeg, cfg);
  if (away && !m.classes.includes('OTHER')) {
    const probs = Object.fromEntries(m.classes.map((c) => [c, 1 / m.classes.length]));
    return { state: 'UNCERTAIN', probs, reason: UNCERTAIN_HEAD_AWAY };
  }
  const probs = away
    ? Object.fromEntries(m.classes.map((c) => [c, c === 'OTHER' ? 1 : 0]))
    : posterior(m, gazeYawDeg, gazePitchDeg, cfg.other_margin_deg, cfg.screen_min_halfwidth_deg);
  const [state, reason] = decideLabel(probs, cfg);
  return { state, probs, reason };
}

// --------------------------------------------------------------------------
// Quality (Python `CalibrationQuality.to_dict()` keys)
// --------------------------------------------------------------------------

export interface CalibrationQualityDict {
  status: 'OK' | 'RETRY_REQUIRED';
  reason: FailReason | null;
  hint: string | null;
  n_camera: number;
  n_bottom: number;
  n_screen: number;
  method: 'reference';
  loo_accuracy: number;
  separability: number;
  centroid_distance: number;
  camera_variance: number;
  bottom_variance: number;
  camera_centroid: number[];
  bottom_centroid: number[];
  anchors_deg: Partial<Record<Cue, number[]>>;
  sigma_deg: number[];
  pair_separation: Record<string, number>;
  camera_capture_radius_deg: number;
  placement: Record<string, unknown> | null;
  warnings: string[];
}

const round4 = (v: number): number => Math.round(v * 1e4) / 1e4;
const pairName = (a: Cue, b: Cue): string => `${a}-${b}`;

function separation(m: ReferenceModelData, a: Cue, b: Cue): number {
  const pa = m.anchors[a]!;
  const pb = m.anchors[b]!;
  return Math.hypot((pa[0] - pb[0]) / m.sigma[0], (pa[1] - pb[1]) / m.sigma[1]);
}

function fail(
  q: CalibrationQualityDict,
  reason: FailReason,
  detail = '',
  warning: string | null = null,
): CalibrationQualityDict {
  q.status = 'RETRY_REQUIRED';
  q.reason = reason;
  const hint = HINTS[reason] + (detail ? ` (${detail})` : '');
  q.hint = warning ? `${hint} ${warning}` : hint;
  return q;
}

/** Each cue sample classified by a model rebuilt without it (UNCERTAIN counts as wrong). */
export function leaveOneOutAccuracy(samples: readonly Sample[], cfg: CalibrationConfig): number {
  const usable = samples.filter(
    (s) => CUES.includes(s.cue) && Number.isFinite(s.yawDeg) && Number.isFinite(s.pitchDeg),
  );
  if (usable.length < 2 * MIN_SAMPLES_FOR_STATS) return 0;
  let correct = 0;
  usable.forEach((sample, i) => {
    const rest = usable.filter((_, j) => j !== i);
    const model = buildReferenceModel(rest, cfg);
    if (!model) return;
    const [label] = decideLabel(
      posterior(
        model,
        sample.yawDeg,
        sample.pitchDeg,
        cfg.other_margin_deg,
        cfg.screen_min_halfwidth_deg,
      ),
      cfg,
    );
    if (label === sample.cue) correct += 1;
  });
  return correct / usable.length;
}

/** Score a calibration and apply the pass rule, most specific reason first. */
export function assessReferenceCalibration(
  samples: readonly Sample[],
  cfg: CalibrationConfig,
  model: ReferenceModelData | null,
): CalibrationQualityDict {
  const values = cueValues(samples);
  const n = {
    CAMERA: values.CAMERA.length,
    SCREEN: values.SCREEN.length,
    BOTTOM: values.BOTTOM.length,
  };
  const q: CalibrationQualityDict = {
    status: 'OK',
    reason: null,
    hint: null,
    n_camera: n.CAMERA,
    n_bottom: n.BOTTOM,
    n_screen: n.SCREEN,
    method: 'reference',
    loo_accuracy: 0,
    separability: 0,
    centroid_distance: 0,
    camera_variance: 0,
    bottom_variance: 0,
    camera_centroid: [],
    bottom_centroid: [],
    anchors_deg: {},
    sigma_deg: [],
    pair_separation: {},
    camera_capture_radius_deg: 0,
    placement: null,
    warnings: [],
  };
  const floor = cfg.min_samples_per_class;
  if (!model || Math.min(n.CAMERA, n.BOTTOM) < MIN_SAMPLES_FOR_STATS)
    return fail(q, 'NOT_ENOUGH_SAMPLES');

  for (const c of CUES) {
    const a = model.anchors[c];
    if (a) q.anchors_deg[c] = [round4(a[0]), round4(a[1])];
  }
  q.sigma_deg = [round4(model.sigma[0]), round4(model.sigma[1])];
  const cam = model.anchors.CAMERA!;
  const bot = model.anchors.BOTTOM!;
  q.camera_centroid = cam.map((v) => (v * Math.PI) / 180);
  q.bottom_centroid = bot.map((v) => (v * Math.PI) / 180);
  const pairs: [Cue, Cue][] = [['CAMERA', 'BOTTOM']];
  for (const [a, b] of [
    ['CAMERA', 'SCREEN'],
    ['SCREEN', 'BOTTOM'],
  ] as [Cue, Cue][]) {
    if (model.anchors[a] && model.anchors[b]) pairs.push([a, b]);
  }
  for (const [a, b] of pairs) q.pair_separation[pairName(a, b)] = round4(separation(model, a, b));
  q.separability = q.pair_separation[pairName('CAMERA', 'BOTTOM')]!;
  q.centroid_distance = Math.hypot(cam[0] - bot[0], cam[1] - bot[1]);
  const meanSq = (rows: [number, number][], c: [number, number]) =>
    rows.reduce((s, p) => s + (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2, 0) /
    Math.max(rows.length, 1);
  q.camera_variance = meanSq(values.CAMERA, cam);
  q.bottom_variance = meanSq(values.BOTTOM, bot);
  const radius = cameraCaptureRadiusDeg(model);
  q.camera_capture_radius_deg = Number.isFinite(radius) ? round4(radius) : 0;

  let warning: string | null = null;
  if (cfg.check_pitch_ordering) {
    const order = CUES.filter((c) => model.anchors[c]);
    const pitches = order.map((c) => model.anchors[c]![1]);
    if (pitches.some((hi, i) => i + 1 < pitches.length && hi <= pitches[i + 1]!)) {
      warning =
        `${INVERTED_PITCH_HINT_PREFIX} the targets are not ordered top to bottom ` +
        `(${order.join(' > ')} expected). Check whether the targets were followed in ` +
        'reverse or the camera is not above the screen.';
      q.warnings.push(warning);
    }
  }
  for (const c of CUES) {
    const s = model.spreads[c];
    if (s && (s[0] > 2 * model.sigma[0] || s[1] > 2 * model.sigma[1])) {
      q.warnings.push(`${c} spread is more than twice the pooled noise`);
    }
  }

  if (Math.min(n.CAMERA, n.BOTTOM) < floor) return fail(q, 'NOT_ENOUGH_SAMPLES', '', warning);
  if (n.SCREEN > 0 && n.SCREEN < floor) return fail(q, 'NOT_ENOUGH_SAMPLES', 'SCREEN', warning);

  const everything = CUES.flatMap((c) => values[c]);
  const std = (k: 0 | 1) => {
    const v = everything.map((p) => p[k]);
    const mean = v.reduce((s, x) => s + x, 0) / v.length;
    return Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / v.length);
  };
  if (std(0) <= 1e-8 && std(1) <= 1e-8) return fail(q, 'DEGENERATE_FEATURES', '', warning);

  const minSep = cfg.min_anchor_separation;
  if (q.separability < minSep) return fail(q, 'CLASS_NOT_SEPARABLE', '', warning);
  for (const [name, sep] of Object.entries(q.pair_separation)) {
    if (sep < minSep) return fail(q, 'CENTROIDS_TOO_CLOSE', name, warning);
  }

  const floorP = cfg.p_max_threshold + SELF_POSTERIOR_MARGIN;
  for (const c of CUES) {
    const a = model.anchors[c];
    if (
      a &&
      (posterior(model, a[0], a[1], cfg.other_margin_deg, cfg.screen_min_halfwidth_deg)[c] ?? 0) <
        floorP
    )
      return fail(q, 'ANCHOR_AMBIGUOUS', c, warning);
  }

  q.loo_accuracy = leaveOneOutAccuracy(samples, cfg);
  if (q.loo_accuracy < cfg.min_loo_accuracy) return fail(q, 'LOW_LOO_ACCURACY', '', warning);

  q.hint = warning;
  return q;
}

/**
 * Build and grade, folding an inseparable SCREEN into CAMERA when that is the
 * only problem (`ReferenceAnchorClassifier.fit` + `_fit_without_screen`).
 */
export function fitReference(
  samples: readonly Sample[],
  cfg: CalibrationConfig,
): { model: ReferenceModelData | null; quality: CalibrationQualityDict } {
  const model = buildReferenceModel(samples, cfg);
  const quality = assessReferenceCalibration(samples, cfg, model);
  if (
    quality.status !== 'OK' &&
    cfg.merge_inseparable_screen &&
    model?.anchors.SCREEN &&
    quality.reason &&
    SCREEN_FIXABLE.has(quality.reason)
  ) {
    const reduced = samples.filter((s) => s.cue !== 'SCREEN');
    const rModel = buildReferenceModel(reduced, cfg);
    const rQuality = assessReferenceCalibration(reduced, cfg, rModel);
    if (rModel && rQuality.status === 'OK') {
      rQuality.n_screen = quality.n_screen;
      rQuality.pair_separation = { ...quality.pair_separation };
      rQuality.warnings.push(SCREEN_MERGED_WARNING);
      rQuality.hint = rQuality.hint
        ? `${rQuality.hint} ${SCREEN_MERGED_WARNING}`
        : SCREEN_MERGED_WARNING;
      return { model: rModel, quality: rQuality };
    }
  }
  return { model, quality };
}

/** Is this a model this engine can use (restored from storage)? */
export function isReferenceModel(value: unknown): value is ReferenceModelData {
  const m = value as Partial<ReferenceModelData> | null;
  return (
    !!m &&
    m.schema === MODEL_SCHEMA &&
    Array.isArray(m.classes) &&
    Array.isArray(m.sigma) &&
    !!m.anchors?.CAMERA &&
    !!m.anchors?.BOTTOM &&
    typeof m.logOtherDensity === 'number'
  );
}
