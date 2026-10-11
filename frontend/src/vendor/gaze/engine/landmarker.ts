/**
 * MediaPipe Face Landmarker, set up the way it has to run inside a module Web
 * Worker (the frontend's `gaze.worker.ts` is one):
 *
 * * `FilesetResolver.forVisionTasks(base, true)` -- the ES-module wasm loader;
 *   the classic loader relies on `importScripts`, which module workers lack.
 * * `base` is an ABSOLUTE url.  Vite's dev server appends `?import` to dynamic
 *   imports of root-relative paths and then refuses files from `public/`;
 *   it leaves `http(s)://` urls alone.  Measured with Vite 8.
 * * VIDEO running mode with strictly increasing timestamps (MediaPipe rejects
 *   repeats), clamped here so two frames in one millisecond cannot crash a take.
 *
 * Assets are served from the caller's `assetDir`, never a CDN:
 * `face_landmarker.task` and `vision_wasm_module_internal.{js,wasm}`.
 */
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import type { LandmarkerResult } from './observe';

export interface FaceDetector {
  detect(frame: TexImageSource, tMs: number): LandmarkerResult;
  close(): void;
}

/**
 * Pay MediaPipe's lazy initialisation (graph allocation, XNNPACK plans) before
 * the first real frame, as `VisionSession.warmup` does in Python: measured in
 * Chrome, the first detections cost 100-200 ms against ~30-40 ms afterwards.
 * Timestamps 0..n-1 are fine -- real frames always come later.
 */
export function warmUp(detector: FaceDetector, frames = 2): void {
  if (typeof OffscreenCanvas === 'undefined') return;
  const canvas = new OffscreenCanvas(64, 64);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.fillStyle = '#808080';
  ctx.fillRect(0, 0, 64, 64);
  for (let i = 0; i < frames; i++) {
    try {
      detector.detect(canvas, i);
    } catch {
      return; // a warm-up failure is not an engine failure; the first real frame will tell
    }
  }
}

export interface LandmarkerOptions {
  /** Where the model and wasm files are served, e.g. `/models/`. */
  assetDir: string;
  numFaces: number;
  delegate?: 'CPU' | 'GPU';
}

function absolute(dir: string): string {
  const base =
    typeof self !== 'undefined' && 'location' in self ? self.location.href : 'http://localhost/';
  return new URL(dir.endsWith('/') ? dir : `${dir}/`, base).href;
}

export async function createFaceDetector(opts: LandmarkerOptions): Promise<FaceDetector> {
  const base = absolute(opts.assetDir);
  const fileset = await FilesetResolver.forVisionTasks(base.replace(/\/$/, ''), true);
  const landmarker = await FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: `${base}face_landmarker.task`,
      delegate: opts.delegate ?? 'CPU',
    },
    runningMode: 'VIDEO',
    numFaces: opts.numFaces,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: true,
  });
  let last = -1;
  return {
    detect(frame, tMs) {
      const ts = Math.max(Math.round(tMs), last + 1);
      last = ts;
      return landmarker.detectForVideo(frame, ts) as unknown as LandmarkerResult;
    },
    close() {
      landmarker.close();
    },
  };
}
