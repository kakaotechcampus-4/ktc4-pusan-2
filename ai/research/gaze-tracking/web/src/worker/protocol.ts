/**
 * Messages between the camera view (`src/camera/view.ts`) and its worker.  A
 * superset of the frontend's `GazeWorkerIn/Out`: the camera view also shows the
 * set-up check, the head circle, the calibration gauge and a face guide, which
 * the frontend contract keeps inside the worker.
 */
import type { CalibrationModel, CalibrationQualityDict, EngineInitFailure } from '../engine';
import type { FrameDecision, OtherMapping } from '../engine/contract';
import type { BaselineCheck, ReanchorStatus } from '../engine/engine';
import type { GaugeStatus } from '../engine/gauge';
import type { PlacementResultDict } from '../engine/placement';
import type { PreconditionReport } from '../engine/preconditions';
import type { SweepStatus } from '../engine/sweep';
import type { Cue, FaceGuide, InvalidReason, StateClass } from '../engine/types';

/** Why something failed: the engine did not start (`EngineInitFailure`), or one frame failed. */
export type EngineFailure = EngineInitFailure | 'FRAME_FAILED';

/** What the worker should do with a frame. */
export type FrameMode = 'preview' | 'check' | 'sweep' | 'calibrate' | 'live';

export type ToWorker =
  | { type: 'init'; assetDir: string; otherAs?: OtherMapping; delegate?: 'CPU' | 'GPU' }
  | { type: 'frame'; bitmap: ImageBitmap; tMs: number; mode: FrameMode }
  | { type: 'startSweep'; tMs: number }
  | { type: 'startCue'; cue: Cue; tMs: number; checkDirection?: boolean }
  | { type: 'estimatePlacement' }
  | { type: 'finishCalibration' }
  | { type: 'resetCalibration' }
  | { type: 'resetPreconditions' }
  | { type: 'reanchor'; tMs: number }
  /** Adopt this session's calibration (`CalibrationModel` from an earlier `calibrated`; memory only). */
  | { type: 'restore'; model: unknown };

export interface FrameSummary {
  tMs: number;
  /** Worker time spent on this frame (landmarks + engine). */
  processMs: number;
  /** Of which MediaPipe face detection. */
  detectMs: number;
  faceValid: boolean;
  invalidReason: InvalidReason | null;
  guide: FaceGuide | null;
  headYawDeg: number;
  headPitchDeg: number;
  imageSize: readonly [number, number];
}

export type FromWorker =
  | { type: 'ready'; version: string; isolated: boolean }
  /** `reason` says why, when known: the engine could not start, or one frame failed (analysis goes on). */
  | { type: 'failed'; message: string; reason?: EngineFailure }
  | {
      type: 'frame';
      frame: FrameSummary;
      check?: PreconditionReport;
      sweep?: SweepStatus;
      gauge?: GaugeStatus;
      /** The screen-centre look against the head circle's centre (calibrate mode). */
      baseline?: BaselineCheck | null;
      decision?: FrameDecision;
      reanchor?: ReanchorStatus;
    }
  | { type: 'placement'; result: PlacementResultDict | null }
  | {
      type: 'calibrated';
      quality: CalibrationQualityDict;
      model: CalibrationModel | null;
      placement: PlacementResultDict | null;
      classes: StateClass[];
    }
  /** `ok: false`: not a model of this engine (other schema or config); nothing changed. */
  | { type: 'restored'; ok: boolean; classes: StateClass[] };
