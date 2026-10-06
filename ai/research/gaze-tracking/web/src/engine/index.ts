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
 * survives `structuredClone`.  It holds face measurements: keep it in memory for the
 * session, never store it (see `CalibrationModel`).
 */
import { makeConfig } from './config';
import { GazeEngine, type EngineSettings } from './engine';
import { createFaceDetector, warmUp, type FaceDetector } from './landmarker';
import { canvasLumaSampler } from './luma';

export interface CreateOptions extends EngineSettings {
  /** Where `face_landmarker.task` and the MediaPipe wasm files are served. */
  assetDir: string;
  /** CPU in production: Python parity is verified on CPU only. GPU is for benchmarks. */
  delegate?: 'CPU' | 'GPU';
  /** Give up loading the model and runtime after this long. Default `DEFAULT_INIT_TIMEOUT_MS`. */
  initTimeoutMs?: number;
}

/** The first load fetches about 16 MB (wasm runtime + model), so the default is generous. */
export const DEFAULT_INIT_TIMEOUT_MS = 60_000;

/**
 * Why the engine could not start.  The host maps every reason to "gaze unavailable" and
 * carries on (the presentation never waits for gaze); the reason is for logs.
 */
export type EngineInitFailure = 'UNSUPPORTED_BROWSER' | 'TIMEOUT' | 'INIT_FAILED';

export class EngineInitError extends Error {
  readonly reason: EngineInitFailure;

  constructor(reason: EngineInitFailure, message: string) {
    super(message);
    this.name = 'EngineInitError';
    this.reason = reason;
  }
}

export interface SupportCheck {
  ok: boolean;
  /** Browser features the engine cannot run without. */
  missing: string[];
}

/**
 * What the engine needs from the browser, cheap enough to call before asking for the camera.
 * Only what it cannot run without is required: without OffscreenCanvas, for one, the brightness
 * checks are skipped and everything else works.  The host checks `Worker` itself.
 */
export function checkSupport(): SupportCheck {
  const missing: string[] = [];
  if (typeof WebAssembly !== 'object') missing.push('WebAssembly');
  if (typeof createImageBitmap !== 'function') missing.push('createImageBitmap');
  return { ok: missing.length === 0, missing };
}

export async function createClassifier(opts: CreateOptions): Promise<GazeEngine<ImageBitmap>> {
  const support = checkSupport();
  if (!support.ok) {
    throw new EngineInitError('UNSUPPORTED_BROWSER', `missing ${support.missing.join(', ')}`);
  }
  const cfg = makeConfig(opts.config);
  const timeoutMs = opts.initTimeoutMs ?? DEFAULT_INIT_TIMEOUT_MS;
  const detector = await new Promise<FaceDetector>((resolve, reject) => {
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      reject(new EngineInitError('TIMEOUT', `model and runtime did not load in ${timeoutMs} ms`));
    }, timeoutMs);
    createFaceDetector({
      assetDir: opts.assetDir,
      numFaces: cfg.preprocess.num_faces,
      delegate: opts.delegate,
    }).then(
      (d) => {
        clearTimeout(timer);
        if (!timedOut) resolve(d);
        else {
          // loaded after the host gave up: release it (nobody is waiting for an error)
          try {
            d.close();
          } catch {
            /* already unusable */
          }
        }
      },
      (err: unknown) => {
        clearTimeout(timer);
        reject(
          new EngineInitError('INIT_FAILED', err instanceof Error ? err.message : String(err)),
        );
      },
    );
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
  CalibrationModel,
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
