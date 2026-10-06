/**
 * Gaze engine (head-pose v1.1) for the browser.
 *
 * The frontend loads this module inside its gaze worker and delegates to it
 * (`frontend/src/workers/modelClassifier.ts`, "A안"):
 *
 *     const { createClassifier } = await import('./gaze/engine');
 *     const impl = await createClassifier({ assetDir: '/models/' });
 *     engineVersion(impl.version);                                 // aiAdapter
 *     toCalibrationResult(...impl.fitCalibration(camera, bottom))   // { quality, model }
 *     toPlacementResult(impl.checkPlacement(camera, screen));
 *     impl.calibrate(ref.model);
 *     toFrameVerdict(impl.classify(frame, tMs));
 *     impl.dispose();
 *
 * Runs without DOM (worker-safe); the calibration `model` is plain data and
 * survives `structuredClone` / IndexedDB.
 */
import { makeConfig } from './config';
import { GazeEngine, type EngineSettings } from './engine';
import { createFaceDetector, warmUp } from './landmarker';
import { canvasLumaSampler } from './luma';

export interface CreateOptions extends EngineSettings {
  /** Where `face_landmarker.task` and the MediaPipe wasm files are served. */
  assetDir: string;
  delegate?: 'CPU' | 'GPU';
}

export async function createClassifier(opts: CreateOptions): Promise<GazeEngine<ImageBitmap>> {
  const cfg = makeConfig(opts.config);
  const detector = await createFaceDetector({
    assetDir: opts.assetDir,
    numFaces: cfg.preprocess.num_faces,
    delegate: opts.delegate,
  });
  warmUp(detector);
  return new GazeEngine<ImageBitmap>({ detector, luma: canvasLumaSampler }, opts);
}

export { GazeEngine } from './engine';
export type {
  BaselineCheck,
  CalibrationOutput,
  EngineDeps,
  EngineSettings,
  ReanchorStatus,
  StoredModel,
} from './engine';
export type {
  AiCalibrationQuality,
  AiGazeDecision,
  AiPlacementCheckResult,
  AiVersionParts,
  FrameDecision,
  OtherMapping,
} from './contract';
export type { ConditionIssue, ConditionState, SceneBaseline } from './condition';
export type { GaugeStatus } from './gauge';
export type { PlacementResultDict } from './placement';
export type { PreconditionReport, PreconditionReason, PreconditionStatus } from './preconditions';
export { directionOfAngle, HeadSweep } from './sweep';
export type { SweepState, SweepStatus } from './sweep';
export type { CalibrationQualityDict, ReferenceModelData } from './reference';
export type { Cue, GazeDirection, Observation, State, StateClass } from './types';
export { GAZE_DIRECTIONS } from './types';
export { CONFIG_HASH, makeConfig, VERSION } from './config';
export type { EngineConfig, EvidenceConfig, SweepConfig } from './config';

// 1 s gaze records: what leaves the device (no image, no landmark).  Reading them
// (coach issues, take summary) is the server's job, not the engine's.
export {
  frameFromDecision,
  GazeEvidenceRecorder,
  GazeSlicer,
  SAMPLE_STATES,
  sampleToDict,
} from './evidence';
export type { GazeFrame, GazeSample, SampleState } from './evidence';
