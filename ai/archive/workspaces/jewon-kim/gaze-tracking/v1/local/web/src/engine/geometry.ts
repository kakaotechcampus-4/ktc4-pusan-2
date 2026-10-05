/**
 * Landmark geometry (port of `vision/preprocess/crops.py` and the parts of
 * `pipeline.py` that pick the main face).  Landmarks are MediaPipe's normalised
 * `{x, y, z}`; pixels are `x * width`, `y * height`.
 */
import type { Bbox, FaceGuide, Point } from './types';

export interface Landmark {
  x: number;
  y: number;
  z?: number;
}

/** Landmark indices, named by the subject's side (MediaPipe's own naming). */
export const LM = {
  NOSE_TIP: 1,
  FOREHEAD: 10,
  CHIN: 152,
  LEFT_EYE_OUTER: 263,
  LEFT_EYE_INNER: 362,
  RIGHT_EYE_OUTER: 33,
  RIGHT_EYE_INNER: 133,
  FACE_SIDE_LEFT: 454,
  FACE_SIDE_RIGHT: 234,
} as const;

const LEFT_IRIS_RING = [474, 475, 476, 477] as const;
const RIGHT_IRIS_RING = [469, 470, 471, 472] as const;

/** Soukupova-Cech layout: outer, two upper lid, inner, two lower lid. */
const EAR_IDS = {
  left: [LM.LEFT_EYE_OUTER, 387, 385, LM.LEFT_EYE_INNER, 380, 373],
  right: [LM.RIGHT_EYE_OUTER, 160, 158, LM.RIGHT_EYE_INNER, 153, 144],
} as const;

const EYE_CORNERS = {
  left: [LM.LEFT_EYE_OUTER, LM.LEFT_EYE_INNER],
  right: [LM.RIGHT_EYE_OUTER, LM.RIGHT_EYE_INNER],
} as const;

/** An eye narrower than this (px) has no usable crop (`_MIN_EYE_WIDTH_PX`). */
const MIN_EYE_WIDTH_PX = 4;

type Size = readonly [number, number];

function px(lm: readonly Landmark[], i: number, size: Size): [number, number] {
  const p = lm[i];
  if (!p) return [NaN, NaN];
  return [p.x * size[0], p.y * size[1]];
}

function dist(a: readonly [number, number], b: readonly [number, number]): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** Fraction of landmarks inside the unit square (presence proxy). */
export function inBoundsFraction(lm: readonly Landmark[]): number {
  if (lm.length === 0) return 0;
  let inside = 0;
  for (const p of lm) if (p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1) inside += 1;
  return inside / lm.length;
}

/** Tight `[x, y, w, h]` pixel box around all landmarks, clamped to the frame. */
export function bboxFromLandmarks(lm: readonly Landmark[], size: Size): Bbox {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of lm) {
    const x = p.x * size[0];
    const y = p.y * size[1];
    if (!Number.isFinite(x) || !Number.isFinite(y)) return [0, 0, 0, 0];
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  if (!Number.isFinite(x0)) return [0, 0, 0, 0];
  const cx0 = Math.max(0, Math.floor(x0));
  const cy0 = Math.max(0, Math.floor(y0));
  const cx1 = Math.min(size[0], Math.ceil(x1));
  const cy1 = Math.min(size[1], Math.ceil(y1));
  return [cx0, cy0, Math.max(0, cx1 - cx0), Math.max(0, cy1 - cy0)];
}

export const bboxArea = (b: Bbox): number => Math.max(0, b[2]) * Math.max(0, b[3]);

export function bboxCentre(b: Bbox, size: Size): Point {
  return [(b[0] + b[2] / 2) / Math.max(size[0], 1), (b[1] + b[3] / 2) / Math.max(size[1], 1)];
}

/** Eye aspect ratio for one side, in pixels (non-square frames skew normalised units). */
export function eyeAspectRatio(
  lm: readonly Landmark[],
  side: 'left' | 'right',
  size: Size,
): number {
  const [p1, p2, p3, p4, p5, p6] = EAR_IDS[side].map((i) => px(lm, i, size)) as [number, number][];
  const span = dist(p1!, p4!);
  if (!(span >= 1e-9)) return 0;
  return (dist(p2!, p6!) + dist(p3!, p5!)) / (2 * span);
}

/** Can this eye be cropped at all (wide enough, centre inside the frame)? */
export function eyeCroppable(lm: readonly Landmark[], side: 'left' | 'right', size: Size): boolean {
  const [outer, inner] = EYE_CORNERS[side].map((i) => px(lm, i, size)) as [number, number][];
  const width = dist(outer!, inner!);
  const cx = (outer![0] + inner![0]) / 2;
  const cy = (outer![1] + inner![1]) / 2;
  return (
    Number.isFinite(width) &&
    width >= MIN_EYE_WIDTH_PX &&
    cx >= 0 &&
    cx < size[0] &&
    cy >= 0 &&
    cy < size[1]
  );
}

/** Mean iris diameter of both eyes in pixels; 0 when unknown (`iris_diameter_px`). */
export function irisDiameterPx(lm: readonly Landmark[], size: Size): number {
  if (lm.length <= Math.max(...LEFT_IRIS_RING)) return 0;
  const diameters: number[] = [];
  for (const ring of [LEFT_IRIS_RING, RIGHT_IRIS_RING]) {
    const pts = ring.map((i) => px(lm, i, size));
    if (!pts.every((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))) continue;
    let best = 0;
    for (let a = 0; a < pts.length; a++) {
      for (let b = a + 1; b < pts.length; b++) best = Math.max(best, dist(pts[a]!, pts[b]!));
    }
    diameters.push(best);
  }
  if (diameters.length === 0) return 0;
  return diameters.reduce((s, d) => s + d, 0) / diameters.length;
}

const inside = (p: readonly [number, number], b: Bbox): boolean =>
  b[0] <= p[0] && p[0] <= b[0] + b[2] && b[1] <= p[1] && p[1] <= b[1] + b[3];

/**
 * Area of the largest *other person's* face over the main face's; 0 when none
 * (`second_face_ratio`).  The main face found a second time (either box's
 * centre inside the other) and a find below the detector floor
 * (`minAreaRatio` of the frame) are not someone else.
 */
export function secondFaceRatio(
  main: Bbox,
  others: readonly Bbox[],
  size: Size,
  minAreaRatio: number,
): number {
  const mainArea = bboxArea(main);
  if (!(mainArea > 0)) return 0;
  const floor = minAreaRatio * size[0] * size[1];
  const mainCentre: [number, number] = [main[0] + main[2] / 2, main[1] + main[3] / 2];
  let best = 0;
  for (const b of others) {
    const area = bboxArea(b);
    if (!(area > 0) || area < floor) continue;
    if (inside([b[0] + b[2] / 2, b[1] + b[3] / 2], main) || inside(mainCentre, b)) continue;
    best = Math.max(best, area / mainArea);
  }
  return best;
}

/**
 * Index of the face to analyse (`select_main_face`): the largest without a
 * hint, the nearest to the hint with one (ties to the larger face).
 */
export function selectMainFace(
  bboxes: readonly Bbox[],
  size: Size,
  hint: Point | null,
): number | null {
  if (bboxes.length === 0) return null;
  const candidates = bboxes.map((b, i) => [b, i] as const).filter(([b]) => bboxArea(b) > 0);
  if (candidates.length === 0) return 0;
  if (!hint || !Number.isFinite(hint[0]) || !Number.isFinite(hint[1])) {
    let best = candidates[0]!;
    for (const c of candidates) {
      const a = bboxArea(c[0]);
      const ba = bboxArea(best[0]);
      if (a > ba || (a === ba && c[1] < best[1])) best = c;
    }
    return best[1];
  }
  const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
  let best: readonly [Bbox, number] | null = null;
  let bestKey: [number, number, number] | null = null;
  for (const c of candidates) {
    const [cx, cy] = bboxCentre(c[0], size);
    const key: [number, number, number] = [
      round6(Math.hypot(cx - hint[0], cy - hint[1])),
      -bboxArea(c[0]),
      c[1],
    ];
    if (
      !bestKey ||
      key[0] < bestKey[0] ||
      (key[0] === bestKey[0] &&
        (key[1] < bestKey[1] || (key[1] === bestKey[1] && key[2] < bestKey[2])))
    ) {
      best = c;
      bestKey = key;
    }
  }
  return best![1];
}

/** The landmarks a UI draws a face guide from. */
export function faceGuide(lm: readonly Landmark[]): FaceGuide | null {
  const at = (i: number): Point | null => {
    const p = lm[i];
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? [p.x, p.y] : null;
  };
  const forehead = at(LM.FOREHEAD);
  const chin = at(LM.CHIN);
  const sideLeft = at(LM.FACE_SIDE_RIGHT); // subject's right = image left
  const sideRight = at(LM.FACE_SIDE_LEFT);
  const eyeLeft = at(LM.RIGHT_EYE_OUTER);
  const eyeRight = at(LM.LEFT_EYE_OUTER);
  const nose = at(LM.NOSE_TIP);
  if (!forehead || !chin || !sideLeft || !sideRight || !eyeLeft || !eyeRight || !nose) return null;
  return { forehead, chin, sideLeft, sideRight, eyeLeft, eyeRight, nose };
}
