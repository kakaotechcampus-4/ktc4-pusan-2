/**
 * Head pose from MediaPipe's facial transformation matrix (port of
 * `vision/preprocess/headpose.py::head_pose_from_matrix`).
 *
 * MediaPipe's metric space is OpenGL-style (+y up, +z toward the viewer); the
 * project convention is OpenCV-style (+y down, +z into the scene), so
 * `R_cv = S R_gl S` with `S = diag(1, -1, -1)`, then `R_cv = Rz(c) Ry(b) Rx(a)`
 * with `yaw = -b`, `pitch = -a`, `roll = c`.
 *
 * The JavaScript task returns `Matrix.data` as a flat array whose order the
 * type does not document.  Measured in Chrome with tasks-vision 1.0.1 it is
 * column-major (the translation sits at indices 12-14); `matrixRowMajor`
 * detects the order from where the translation is, so a future change of
 * layout cannot silently mirror the yaw.
 */
import type { HeadPose } from './types';

const GIMBAL_EPS = 1e-6;
/** Vertical field of view MediaPipe's face geometry assumes (63 deg, verified in Python). */
export const DEFAULT_VERTICAL_FOV_DEG = 63;

export const UNMEASURED_POSE: HeadPose = Object.freeze({
  yaw: NaN,
  pitch: NaN,
  roll: NaN,
  reprojectionError: Infinity,
  depthCm: NaN,
});

/** Assumed pinhole focal length in pixels for a frame of this height. */
export function focalLengthPx(height: number): number {
  return height / 2 / Math.tan((DEFAULT_VERTICAL_FOV_DEG * Math.PI) / 180 / 2);
}

/** The 4x4 matrix as `m[row][col]`, whichever order the flat data used. */
export function matrixRowMajor(data: ArrayLike<number>): number[][] {
  if (data.length < 16)
    throw new Error(`transformation matrix needs 16 values, got ${data.length}`);
  const v = Array.from(data, Number);
  // Column-major keeps the translation in the last four entries (12-14);
  // row-major keeps it in the last column (3, 7, 11).
  const colMajorT = Math.hypot(v[12]!, v[13]!, v[14]!);
  const rowMajorT = Math.hypot(v[3]!, v[7]!, v[11]!);
  const colMajor = colMajorT >= rowMajorT;
  const m: number[][] = [];
  for (let r = 0; r < 4; r++) {
    m.push([0, 1, 2, 3].map((c) => (colMajor ? v[c * 4 + r]! : v[r * 4 + c]!)));
  }
  return m;
}

/** `(yaw, pitch, roll)` from a face->camera rotation in the OpenCV frame. */
export function eulerFromCameraRotation(r: number[][]): [number, number, number] {
  const r00 = r[0]![0]!;
  const r10 = r[1]![0]!;
  const r20 = r[2]![0]!;
  const cosB = Math.sqrt(r00 * r00 + r10 * r10);
  let a: number;
  let c: number;
  if (cosB > GIMBAL_EPS) {
    a = Math.atan2(r[2]![1]!, r[2]![2]!);
    c = Math.atan2(r10, r00);
  } else {
    a = Math.atan2(-r[1]![2]!, r[1]![1]!);
    c = 0;
  }
  const b = Math.atan2(-r20, cosB);
  return [-b, -a, c];
}

export function headPoseFromMatrix(data: ArrayLike<number>): HeadPose {
  const m = matrixRowMajor(data);
  const s = [1, -1, -1];
  const rcv = [0, 1, 2].map((i) => [0, 1, 2].map((j) => s[i]! * s[j]! * m[i]![j]!));
  const [yaw, pitch, roll] = eulerFromCameraRotation(rcv);
  if (![yaw, pitch, roll].every(Number.isFinite)) return UNMEASURED_POSE;
  // The camera looks down -z (GL frame), so the distance to the face is -t_z.
  return { yaw, pitch, roll, reprojectionError: 0, depthCm: -m[2]![3]! };
}
