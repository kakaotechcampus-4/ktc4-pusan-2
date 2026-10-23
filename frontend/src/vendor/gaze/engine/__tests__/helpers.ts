/**
 * Test helpers: fixture decoding, the shared observation format of
 * `tools/make_fixtures.py::make_obs`, and a fake face detector.
 */
import { expect } from 'vitest';
import { focalLengthPx } from '../headpose';
import type { LandmarkerResult } from '../observe';
import type { FaceDetector } from '../landmarker';
import { toRad, type Observation } from '../types';
import fixture from './fixtures/parity.json';

/** JSON cannot hold NaN/Infinity; the generator writes them as strings. */
export function revive<T>(value: unknown): T {
  if (value === 'NaN') return NaN as T;
  if (value === 'Infinity') return Infinity as T;
  if (value === '-Infinity') return -Infinity as T;
  if (Array.isArray(value)) return value.map((v) => revive(v)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, revive(v)])) as T;
  }
  return value as T;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const FX: any = revive(fixture);

/** Numbers (deeply) equal within `tol`, everything else strictly. */
export function close(actual: unknown, expected: unknown, tol = 1e-9, path = '$'): void {
  if (typeof expected === 'number') {
    expect(typeof actual, path).toBe('number');
    const a = actual as number;
    if (Number.isNaN(expected)) return void expect(Number.isNaN(a), path).toBe(true);
    if (!Number.isFinite(expected)) return void expect(a, path).toBe(expected);
    const scale = Math.max(1, Math.abs(expected));
    expect(Math.abs(a - expected), `${path}: ${a} vs ${expected}`).toBeLessThanOrEqual(tol * scale);
    return;
  }
  if (Array.isArray(expected)) {
    expect(Array.isArray(actual), path).toBe(true);
    expect((actual as unknown[]).length, path).toBe(expected.length);
    expected.forEach((e, i) => close((actual as unknown[])[i], e, tol, `${path}[${i}]`));
    return;
  }
  if (expected && typeof expected === 'object') {
    for (const [k, e] of Object.entries(expected))
      close((actual as Record<string, unknown>)?.[k], e, tol, `${path}.${k}`);
    return;
  }
  expect(actual, path).toEqual(expected);
}

/** `make_obs` from the fixture generator, field for field. */
export interface ObsSpec {
  t: number;
  reason?: string | null;
  valid?: boolean;
  yaw?: number;
  pitch?: number;
  ear?: number;
  brightness?: number;
  background?: number;
  bbox?: [number, number, number, number];
  second?: number;
  iris?: number;
  /** Face-mesh distance in cm (0 or absent: unknown). */
  depth?: number;
  /** The head pose was not measured (infinite reprojection error). */
  unmeasured?: boolean;
}

export function makeObs(d: ObsSpec): Observation {
  const hasFace = d.reason !== 'NO_FACE';
  const ear = d.ear ?? 0.3;
  const face = d.brightness ?? 120;
  const background = d.background ?? 100;
  return {
    tMs: d.t,
    faceValid: (d.valid ?? true) && (d.reason ?? null) === null,
    invalidReason: (d.reason ?? null) as Observation['invalidReason'],
    presence: hasFace ? 1 : 0,
    headPose: {
      yaw: toRad(d.yaw ?? 0),
      pitch: toRad(d.pitch ?? 15),
      roll: 0,
      reprojectionError: d.unmeasured ? Infinity : 0,
      depthCm: d.depth ?? 0,
    },
    quality: {
      leftEyeOpenness: ear,
      rightEyeOpenness: ear,
      minEyeOpenness: ear,
      landmarkVisibility: 1,
      faceAreaRatio: 0,
      touchesBorder: false,
      faceBrightness: face,
      backgroundBrightness: background,
      backlightRatio: face > 1e-6 ? background / face : 0,
    },
    faceBbox: hasFace ? (d.bbox ?? [250, 150, 140, 180]) : null,
    imageSize: [640, 480],
    scene: {
      nFaces: hasFace ? 1 : 0,
      secondFaceAreaRatio: d.second ?? 0,
      irisDiameterPx: hasFace ? (d.iris ?? 11) : 0,
    },
    guide: null,
  };
}

/** A MediaPipe transformation matrix (column-major, like the JS task) for a head pose in degrees. */
export function poseMatrix(yawDeg: number, pitchDeg: number, rollDeg = 0, depthCm = 62): number[] {
  // R_cv = Rz(c) Ry(b) Rx(a) with yaw = -b, pitch = -a, roll = c; R_gl = S R_cv S.
  const a = toRad(-pitchDeg);
  const b = toRad(-yawDeg);
  const c = toRad(rollDeg);
  const rx = [
    [1, 0, 0],
    [0, Math.cos(a), -Math.sin(a)],
    [0, Math.sin(a), Math.cos(a)],
  ];
  const ry = [
    [Math.cos(b), 0, Math.sin(b)],
    [0, 1, 0],
    [-Math.sin(b), 0, Math.cos(b)],
  ];
  const rz = [
    [Math.cos(c), -Math.sin(c), 0],
    [Math.sin(c), Math.cos(c), 0],
    [0, 0, 1],
  ];
  const mul = (p: number[][], q: number[][]) =>
    p.map((row) => [0, 1, 2].map((j) => row.reduce((s, v, k) => s + v * q[k]![j]!, 0)));
  const rcv = mul(mul(rz, ry), rx);
  const s = [1, -1, -1];
  const rgl = rcv.map((row, i) => row.map((v, j) => s[i]! * s[j]! * v));
  const t = [-1.5, 21, -depthCm];
  const m = [0, 1, 2, 3].map((r) => (r < 3 ? [...rgl[r]!, t[r]!] : [0, 0, 0, 1]));
  const out: number[] = [];
  for (let col = 0; col < 4; col++) for (let row = 0; row < 4; row++) out.push(m[row]![col]!);
  return out;
}

/** A frame for the fake detector: what it "shows". */
export interface FakeFrame {
  width: number;
  height: number;
  yaw: number;
  pitch: number;
  /** No face at all. */
  empty?: boolean;
  /** A second face next to the main one (area ratio ~1). */
  twoFaces?: boolean;
  /** The presenter moved: the face's offset in the picture, normalised `[x, y]`. */
  moved?: [number, number];
  /** Eyelids down on the eye line (eye openness ~0), as when reading far below. */
  eyesClosed?: boolean;
  /** Distance factor: 1 at the calibration distance, 0.5 = twice as far. */
  scale?: number;
  /** MediaPipe returns the main face a second time, slightly offset. */
  duplicate?: boolean;
  /** A tiny false find in the background (below the detector floor). */
  tinyFind?: boolean;
  /** Face luminance for the luma stub (`lumaOf`). */
  brightness?: number;
}

/** A luma sampler that reports the frame's own `brightness` (120 by default). */
export const lumaOf = (f: FakeFrame) => ({
  measure: () => ({ face: f.brightness ?? 120, background: 100 }),
});

/** A plausible frontal face (the fixture generator's first synthetic face). */
export const FACE_LANDMARKS: { x: number; y: number; z: number }[] =
  FX.geometry.faces[0].landmarks.map(([x, y, z]: number[]) => ({ x, y, z }));
/** Its iris diameter in pixels at 640x480. */
const FACE_IRIS_PX: number = FX.geometry.faces[0].iris;
/** The face-mesh distance that agrees with that iris size (cm). */
const FACE_DEPTH_CM = (focalLengthPx(480) * 1.17) / FACE_IRIS_PX;
const FACE_CX = FACE_LANDMARKS.reduce((s, p) => s + p.x, 0) / FACE_LANDMARKS.length;
const FACE_CY = FACE_LANDMARKS.reduce((s, p) => s + p.y, 0) / FACE_LANDMARKS.length;

/** The face scaled about its centre by `k` and moved by `(dx, dy)` (normalised). */
const placed = (k: number, dx: number, dy: number) =>
  FACE_LANDMARKS.map((p) => ({
    x: FACE_CX + (p.x - FACE_CX) * k + dx,
    y: FACE_CY + (p.y - FACE_CY) * k + dy,
    z: p.z * k,
  }));

export class FakeDetector implements FaceDetector {
  calls: number[] = [];
  closed = false;
  detect(frame: TexImageSource, tMs: number): LandmarkerResult {
    const f = frame as unknown as FakeFrame;
    this.calls.push(tMs);
    if (f.empty) return { faceLandmarks: [], facialTransformationMatrixes: [] };
    // A head turning in place slides the face toward the turn (~8 cm in front of
    // the rotation centre), as a real camera sees it.
    const k = f.scale ?? 1;
    const pxPerCm = (FACE_IRIS_PX / 1.17) * k;
    const yaw = (f.yaw * Math.PI) / 180;
    const pitch = (f.pitch * Math.PI) / 180;
    const dx = (8 * Math.sin(yaw) * Math.cos(pitch) * pxPerCm) / f.width + (f.moved?.[0] ?? 0);
    const dy = (-8 * Math.sin(pitch) * pxPerCm) / f.height + (f.moved?.[1] ?? 0);
    const faces = [placed(k, dx, dy)];
    if (f.eyesClosed) {
      // Every lid point drops onto the line between its eye's corners.
      const main = faces[0]!;
      for (const [outer, inner, lids] of [
        [263, 362, [387, 385, 380, 373]],
        [33, 133, [160, 158, 153, 144]],
      ] as const) {
        const lineY = (main[outer]!.y + main[inner]!.y) / 2;
        for (const i of lids) main[i] = { ...main[i]!, y: lineY };
      }
    }
    const matrices = [{ data: poseMatrix(f.yaw, f.pitch, 0, FACE_DEPTH_CM / k) }];
    if (f.duplicate) {
      faces.push(placed(k * 0.92, dx + 0.01, dy + 0.008));
      matrices.push({ data: poseMatrix(f.yaw, f.pitch, 0, FACE_DEPTH_CM / k) });
    }
    if (f.tinyFind) {
      faces.push(placed(0.22, 0.12 - FACE_CX, 0.12 - FACE_CY));
      matrices.push({ data: poseMatrix(0, 0) });
    }
    if (f.twoFaces) {
      faces.push(FACE_LANDMARKS.map((p) => ({ ...p, x: p.x - 0.35 })));
      matrices.push({ data: poseMatrix(0, 0) });
    }
    return { faceLandmarks: faces, facialTransformationMatrixes: matrices };
  }
  close(): void {
    this.closed = true;
  }
}

export const frame = (yaw: number, pitch: number, extra: Partial<FakeFrame> = {}): FakeFrame => ({
  width: 640,
  height: 480,
  yaw,
  pitch,
  ...extra,
});
