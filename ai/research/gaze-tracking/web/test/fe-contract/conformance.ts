/**
 * What `frontend/src/workers/modelClassifier.ts` becomes once its TODOs are
 * filled with this engine -- written here, against the frontend's own contract
 * and adapter files (read-only imports), so the connection is compiled and run
 * before anyone touches the frontend.
 *
 * In the frontend the factory is
 *     () => import('./gaze/engine').then((m) => m.createClassifier({ assetDir: '/models/' }))
 * Tests pass a factory that builds the engine around a fake face detector.
 */
import {
  engineVersion,
  toCalibrationResult,
  toFrameVerdict,
  toPlacementResult,
} from '@fe/workers/aiAdapter';
import type {
  CalibrationResult,
  FrameVerdict,
  GazeClassifier,
  PlacementResult,
  ZoneReference,
} from '@fe/workers/gaze.contract';
import type { Ms } from '@fe/types/api';
import type { GazeEngine } from '../../src/engine';

export class EngineBackedClassifier implements GazeClassifier {
  #factory: () => Promise<GazeEngine<ImageBitmap>>;
  #impl: GazeEngine<ImageBitmap> | null = null;
  #version = 'gaze-module@unloaded';
  #ref: ZoneReference | null = null;

  constructor(factory: () => Promise<GazeEngine<ImageBitmap>>) {
    this.#factory = factory;
  }

  get version(): string {
    return this.#version;
  }

  async init(): Promise<void> {
    this.#impl = await this.#factory();
    this.#version = engineVersion(this.#impl.version);
  }

  fitCalibration(
    camera: readonly ImageBitmap[],
    bottom: readonly ImageBitmap[],
  ): CalibrationResult {
    if (!this.#impl) return { ok: false, reason: 'ENGINE_ERROR' };
    const { quality, model } = this.#impl.fitCalibration(camera, bottom);
    return toCalibrationResult(quality, model);
  }

  checkPlacement(camera: readonly ImageBitmap[], screen: readonly ImageBitmap[]): PlacementResult {
    if (!this.#impl) return { placement: 'INCONCLUSIVE', supported: false, reason: 'ENGINE_ERROR' };
    return toPlacementResult(this.#impl.checkPlacement(camera, screen));
  }

  calibrate(ref: ZoneReference): void {
    this.#ref = ref;
    this.#impl?.calibrate(ref.model);
  }

  classify(frame: ImageBitmap, tMs: Ms): FrameVerdict | null {
    if (!this.#ref || !this.#impl) return null;
    return toFrameVerdict(this.#impl.classify(frame, tMs));
  }

  dispose(): void {
    this.#ref = null;
    this.#impl?.dispose();
  }
}
