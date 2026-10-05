/**
 * Camera placement from the lens and screen-centre anchors (port of
 * `vision/runtime/placement.py::placement_from_anchors`).  Output keys follow
 * `PlacementCheckResult.to_dict()`, which is what the frontend adapter reads
 * (`placement`, `supported`, `reason`).
 */
import type { CalibrationConfig, PlacementConfig } from './config';
import { median } from './math';
import { robustSigma } from './reference';

export type CameraPlacement = 'TOP' | 'BOTTOM' | 'SIDE_LEFT' | 'SIDE_RIGHT' | 'INCONCLUSIVE';
export type PlacementReason =
  | 'OK'
  | 'NOT_ENOUGH_SAMPLES'
  | 'TARGETS_NOT_SEPARATED'
  | 'DISPLACEMENT_TOO_SMALL'
  | 'AMBIGUOUS_AXIS';

export interface PlacementResultDict {
  placement: CameraPlacement;
  supported: boolean;
  delta_pitch_deg: number;
  delta_yaw_deg: number;
  n_camera: number;
  n_screen: number;
  reason: PlacementReason;
  hint: string;
  mode: 'anchors';
  loo_accuracy: number;
  axis_dominance: number;
  axis: 'vertical' | 'horizontal' | 'unknown';
  separation: number;
  offset_deg: number;
  camera_centroid_deg: number[];
  screen_centroid_deg: number[];
}

const HINTS: Record<Exclude<CameraPlacement, 'INCONCLUSIVE'>, string> = {
  TOP: 'The webcam is estimated to be above the screen, like a laptop camera.',
  BOTTOM:
    "The webcam is estimated to be below the screen. This is advisory; continue to calibration to learn the user's actual CAMERA and BOTTOM looks.",
  SIDE_LEFT:
    'The webcam is estimated to be left of the screen. Centre it above the display when practical, then use calibration to learn the actual looks.',
  SIDE_RIGHT:
    'The webcam is estimated to be right of the screen. Centre it above the display when practical, then use calibration to learn the actual looks.',
};

const INCONCLUSIVE_HINTS: Record<Exclude<PlacementReason, 'OK'>, string> = {
  NOT_ENOUGH_SAMPLES: 'Not enough usable frames. Keep your face in view and well lit, then retry.',
  TARGETS_NOT_SEPARATED:
    'The two cues looked the same to the model. Look right into the lens for the first cue, then clearly at the middle of the screen for the second.',
  DISPLACEMENT_TOO_SMALL:
    'Camera and screen centre are almost the same direction. Either the camera sits in the middle of the display, or you are sitting very far away.',
  AMBIGUOUS_AXIS:
    "The camera looks diagonally offset from the screen centre, so 'below' and 'beside' fit equally well. Centre the webcam above the screen and retry.",
};

const round = (v: number, dp: number): number => Math.round(v * 10 ** dp) / 10 ** dp;

/** Placement from per-frame `(yaw, pitch)` degrees of the two looks. */
export function placementFromSamples(
  camera: readonly (readonly [number, number])[],
  screen: readonly (readonly [number, number])[],
  calibration: CalibrationConfig,
  cfg: PlacementConfig,
): PlacementResultDict | null {
  if (camera.length === 0 || screen.length === 0) return null;
  const cam: [number, number] = [median(camera.map((p) => p[0])), median(camera.map((p) => p[1]))];
  const scr: [number, number] = [median(screen.map((p) => p[0])), median(screen.map((p) => p[1]))];
  const resYaw = [...camera.map((p) => p[0] - cam[0]), ...screen.map((p) => p[0] - scr[0])];
  const resPitch = [...camera.map((p) => p[1] - cam[1]), ...screen.map((p) => p[1] - scr[1])];
  const sigma: [number, number] = [
    robustSigma(resYaw, calibration),
    robustSigma(resPitch, calibration),
  ];
  return placementFromAnchors(cam, scr, camera.length, screen.length, sigma, cfg);
}

export function placementFromAnchors(
  cam: readonly [number, number],
  scr: readonly [number, number],
  nCamera: number,
  nScreen: number,
  sigma: readonly [number, number],
  cfg: PlacementConfig,
): PlacementResultDict {
  const dYaw = scr[0] - cam[0];
  const dPitch = scr[1] - cam[1];
  const base = {
    delta_pitch_deg: round(dPitch, 2),
    delta_yaw_deg: round(dYaw, 2),
    n_camera: nCamera,
    n_screen: nScreen,
    mode: 'anchors' as const,
    loo_accuracy: 0,
    axis_dominance: 0,
    axis: 'unknown' as PlacementResultDict['axis'],
    separation: 0,
    offset_deg: round(Math.hypot(dYaw, dPitch), 2),
    camera_centroid_deg: cam.map((v) => round(v, 2)),
    screen_centroid_deg: scr.map((v) => round(v, 2)),
  };
  const inconclusive = (reason: Exclude<PlacementReason, 'OK'>): PlacementResultDict => ({
    ...base,
    placement: 'INCONCLUSIVE',
    supported: false,
    reason,
    hint: INCONCLUSIVE_HINTS[reason],
  });

  if (Math.min(nCamera, nScreen) < cfg.min_samples_per_target)
    return inconclusive('NOT_ENOUGH_SAMPLES');

  const sYaw = Math.max(sigma[0], 1e-6);
  const sPitch = Math.max(sigma[1], 1e-6);
  const sep = Math.hypot(dYaw / sYaw, dPitch / sPitch);
  base.separation = round(sep, 3);
  if (sep < cfg.min_separation) return inconclusive('TARGETS_NOT_SEPARATED');

  const vertical = Math.abs(dPitch) >= Math.abs(dYaw);
  const [big, small] = vertical
    ? [Math.abs(dPitch), Math.abs(dYaw)]
    : [Math.abs(dYaw), Math.abs(dPitch)];
  const dominance = small > 1e-9 ? big / small : Infinity;
  base.axis_dominance = round(Math.min(dominance, 999), 3);
  base.axis = vertical ? 'vertical' : 'horizontal';
  if (dominance < cfg.anchor_min_axis_dominance) return inconclusive('AMBIGUOUS_AXIS');

  const axisDelta = vertical ? dPitch : dYaw;
  if (Math.abs(axisDelta) < cfg.min_delta_deg) return inconclusive('DISPLACEMENT_TOO_SMALL');
  // pitch > 0 is UP: the screen centre below the lens means the camera is on top.
  // Raw frame: the presenter's right appears on the image left.
  const placement: Exclude<CameraPlacement, 'INCONCLUSIVE'> = vertical
    ? axisDelta < 0
      ? 'TOP'
      : 'BOTTOM'
    : axisDelta > 0
      ? 'SIDE_RIGHT'
      : 'SIDE_LEFT';
  return {
    ...base,
    placement,
    supported: placement === 'TOP',
    reason: 'OK',
    hint: HINTS[placement],
  };
}
