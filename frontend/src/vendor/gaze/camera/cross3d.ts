/**
 * The face cross in 3D: the face's midline and eye line drawn on an ellipsoid
 * that turns with the head, so the lines bend as it turns (a flat cross only
 * slides).  Only the half facing the camera is drawn.
 *
 * Display axes: x right, y down, z toward the viewer.  The head rotation is
 * `Rz(roll) · Ry(-yaw) · Rx(pitch)` -- yaw > 0 turns toward the image right,
 * which the mirrored preview shows on the left; pitch > 0 is up; roll is the
 * face axis' tilt on screen, measured from the landmarks.
 */
export interface FaceShape {
  cx: number;
  cy: number;
  /** Half width and half height of the face on screen, px. */
  rx: number;
  ry: number;
  /** Tilt of the face axis on screen, radians (0 = upright). */
  angle: number;
  /** Eye line offset from the centre along the face axis, px (negative = above). */
  eye: number;
}

const SPAN_DEG = 72;
const STEP_DEG = 6;
/** Face depth as a share of its half width. */
const DEPTH = 0.8;

export function crossCurves(
  s: FaceShape,
  yawDeg: number,
  pitchDeg: number,
): { mid: [number, number][]; eye: [number, number][] } {
  const a = (-yawDeg * Math.PI) / 180;
  const b = (pitchDeg * Math.PI) / 180;
  const [ca, sa, cb, sb] = [Math.cos(a), Math.sin(a), Math.cos(b), Math.sin(b)];
  const [cr, sr] = [Math.cos(s.angle), Math.sin(s.angle)];
  const rz = DEPTH * s.rx;
  /** Rotate a face-local point and project it (orthographic); null when facing away. */
  const place = (x: number, y: number, z: number): [number, number] | null => {
    const y1 = y * cb - z * sb; // Rx(pitch)
    const z1 = y * sb + z * cb;
    const x2 = x * ca + z1 * sa; // Ry(-yaw)
    const z2 = -x * sa + z1 * ca;
    if (z2 < -0.05 * rz) return null;
    return [s.cx + x2 * cr - y1 * sr, s.cy + x2 * sr + y1 * cr]; // Rz(roll) on screen
  };
  const ts: number[] = [];
  for (let t = -SPAN_DEG; t <= SPAN_DEG; t += STEP_DEG) ts.push((t * Math.PI) / 180);
  const keep = (pts: ([number, number] | null)[]) =>
    pts.filter((p): p is [number, number] => p !== null);
  const mid = keep(ts.map((t) => place(0, s.ry * Math.sin(t), rz * Math.cos(t))));
  const k = Math.sqrt(Math.max(0, 1 - (s.eye / s.ry) ** 2));
  const eye = keep(ts.map((t) => place(s.rx * k * Math.sin(t), s.eye, rz * k * Math.cos(t))));
  return { mid, eye };
}

export const toPoints = (pts: [number, number][]): string =>
  pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
