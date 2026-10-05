/**
 * What crosses into the frontend.
 *
 * The frontend's `workers/aiAdapter.ts` already maps the Python engine's
 * `to_dict()` output onto its `GazeClassifier` contract (2-point calibration,
 * zones CAMERA / BOTTOM / UNCERTAIN).  The engine therefore hands out objects
 * with exactly those snake_case keys, plus extra fields the adapter ignores.
 * The types below restate the adapter's input shapes so this package can be
 * type-checked against them without importing frontend code.
 */
import type { CalibrationConfig } from './config';
import type { ConditionState } from './condition';
import type { GazeDirection, State, StateClass } from './types';

/** `aiAdapter.AiCalibrationQuality` -- the fields the frontend reads. */
export interface AiCalibrationQuality {
  status: 'OK' | 'RETRY_REQUIRED';
  reason: string | null;
  hint: string | null;
  separability: number;
  loo_accuracy: number;
}

/** `aiAdapter.AiPlacementCheckResult`. */
export interface AiPlacementCheckResult {
  placement: string;
  supported: boolean;
  reason: string;
}

/** Frontend zone (`GazeZone` in `frontend/src/types/api.ts`). */
export type ContractZone = 'CAMERA' | 'BOTTOM' | 'UNCERTAIN';

/** `aiAdapter.AiGazeDecision` -- one unsmoothed frame decision. */
export interface AiGazeDecision {
  label: ContractZone;
  p_camera: number;
  p_bottom: number;
  face_valid: boolean;
}

/** `aiAdapter.AiVersionParts`. */
export interface AiVersionParts {
  modelVersion: string;
  gazeBackbone: string;
  gazeClassifier: string;
}

/**
 * Where the four engine classes land in the frontend's three zones.
 *
 *   CAMERA        -> CAMERA
 *   SCREEN, BOTTOM -> BOTTOM   (the frontend's BOTTOM is "the screen": script and slides)
 *   OTHER         -> `otherAs` (UNCERTAIN by default; see web/README.md, "OTHER")
 */
export type OtherMapping = 'UNCERTAIN' | 'BOTTOM';

/** A frame decision: the contract fields plus the engine's own view. */
export interface FrameDecision extends AiGazeDecision {
  t_ms: number;
  /** The engine's 4-class decision (before mapping to the contract zones). */
  state: State;
  probs: Partial<Record<StateClass, number>> | null;
  uncertain_reason: string | null;
  head_yaw_deg: number;
  head_pitch_deg: number;
  /** For an OTHER decision: which way outside the screen area (presenter-centric). */
  direction: GazeDirection | null;
  /** `[rightDeg, upDeg]` outside the calibrated screen area (`[0, 0]` inside); null without a gaze. */
  offset_deg: [number, number] | null;
  /** `[rightDeg, upDeg]` from the screen-centre look, inside the screen area too; null without a gaze. */
  aim_deg: [number, number] | null;
  /** Live measurement conditions; null before calibration. */
  condition: ConditionState | null;
}

/**
 * The contract label from the class posterior: the doc 5-4 rule applied to the
 * zone groups (CAMERA, SCREEN+BOTTOM, OTHER), not to the four classes.  A look
 * between the screen centre and the script is confidently "the screen" even
 * when it is not confidently either of the two.
 */
export function contractDecision(
  probs: Partial<Record<StateClass, number>>,
  cfg: CalibrationConfig,
  otherAs: OtherMapping,
): { label: ContractZone; p_camera: number; p_bottom: number; reason: string | null } {
  const pCamera = probs.CAMERA ?? 0;
  const pBottom =
    (probs.SCREEN ?? 0) + (probs.BOTTOM ?? 0) + (otherAs === 'BOTTOM' ? (probs.OTHER ?? 0) : 0);
  const pOther = otherAs === 'BOTTOM' ? 0 : (probs.OTHER ?? 0);
  const groups: [ContractZone | 'OTHER', number][] = [
    ['CAMERA', pCamera],
    ['BOTTOM', pBottom],
    ['OTHER', pOther],
  ];
  const ranked = groups.map((g) => g[1]).sort((a, b) => b - a);
  const pMax = ranked[0]!;
  const margin = pMax - ranked[1]!;
  if (pMax < cfg.p_max_threshold)
    return { label: 'UNCERTAIN', p_camera: pCamera, p_bottom: pBottom, reason: 'LOW_CONFIDENCE' };
  if (margin < cfg.margin_threshold)
    return { label: 'UNCERTAIN', p_camera: pCamera, p_bottom: pBottom, reason: 'LOW_MARGIN' };
  let best = groups[0]!;
  for (const g of groups) if (g[1] > best[1]) best = g;
  if (best[0] === 'OTHER')
    return { label: 'UNCERTAIN', p_camera: pCamera, p_bottom: pBottom, reason: 'OTHER' };
  return { label: best[0], p_camera: pCamera, p_bottom: pBottom, reason: null };
}
