/// <reference lib="webworker" />
/**
 * The camera view's gaze worker: frames in, small summaries out.  The engine is
 * the same module the frontend will load (`src/engine`); this file only routes
 * messages, exactly like the frontend's `gaze.worker.ts` does with its contract.
 *
 * Every `frame` message is answered once, after the bitmap is closed -- that
 * answer is the main thread's back-pressure signal, so at most one frame is
 * ever in flight.
 */
import { createClassifier, type GazeEngine } from '../engine';
import type { Observation } from '../engine/types';
import { toDeg } from '../engine/types';
import type { FrameSummary, FromWorker, ToWorker } from './protocol';

const scope = self as unknown as DedicatedWorkerGlobalScope;
let engine: GazeEngine<ImageBitmap> | null = null;

const post = (msg: FromWorker) => scope.postMessage(msg);

function summary(obs: Observation, started: number): FrameSummary {
  return {
    tMs: obs.tMs,
    processMs: performance.now() - started,
    detectMs: engine?.lastTiming.detectMs ?? 0,
    faceValid: obs.faceValid,
    invalidReason: obs.invalidReason,
    guide: obs.guide,
    headYawDeg: toDeg(obs.headPose.yaw),
    headPitchDeg: toDeg(obs.headPose.pitch),
    imageSize: obs.imageSize,
  };
}

scope.onmessage = async (event: MessageEvent<ToWorker>) => {
  const msg = event.data;
  try {
    switch (msg.type) {
      case 'init': {
        engine = await createClassifier({
          assetDir: msg.assetDir,
          otherAs: msg.otherAs,
          delegate: msg.delegate,
        });
        const v = engine.version;
        post({
          type: 'ready',
          version: `${v.modelVersion}+${v.gazeBackbone}+${v.gazeClassifier}`,
          isolated: scope.crossOriginIsolated,
        });
        return;
      }
      case 'frame': {
        const started = performance.now();
        try {
          if (!engine) throw new Error('engine not ready');
          if (msg.mode === 'check') {
            const { report, observation } = engine.checkPreconditions(msg.bitmap, msg.tMs);
            post({ type: 'frame', frame: summary(observation, started), check: report });
          } else if (msg.mode === 'sweep') {
            const { status, observation } = engine.offerSweepFrame(msg.bitmap, msg.tMs);
            post({ type: 'frame', frame: summary(observation, started), sweep: status });
          } else if (msg.mode === 'calibrate') {
            const { status, observation } = engine.offerCalibrationFrame(msg.bitmap, msg.tMs);
            post({
              type: 'frame',
              frame: summary(observation, started),
              gauge: status,
              baseline: engine.baselineCheck,
            });
          } else if (msg.mode === 'live') {
            const observation = engine.observe(msg.bitmap, msg.tMs);
            const decision = engine.decide(observation);
            post({
              type: 'frame',
              frame: summary(observation, started),
              decision,
              reanchor: engine.reanchorStatus,
            });
          } else {
            post({ type: 'frame', frame: summary(engine.observe(msg.bitmap, msg.tMs), started) });
          }
        } finally {
          msg.bitmap.close();
        }
        return;
      }
      case 'startSweep':
        engine?.startSweep(msg.tMs);
        return;
      case 'startCue':
        engine?.startCue(msg.cue, msg.tMs, msg.checkDirection ?? true);
        return;
      case 'estimatePlacement':
        post({ type: 'placement', result: engine?.estimatePlacement() ?? null });
        return;
      case 'finishCalibration': {
        if (!engine) return;
        const { quality, model } = engine.finishCalibration();
        post({
          type: 'calibrated',
          quality,
          model,
          placement: engine.placement,
          classes: [...engine.classes],
        });
        return;
      }
      case 'resetCalibration':
        engine?.resetCalibration();
        return;
      case 'resetPreconditions':
        engine?.resetPreconditions();
        return;
      case 'reanchor':
        engine?.beginReanchor(msg.tMs);
        return;
      case 'restore': {
        const ok = engine?.calibrate(msg.model) ?? false;
        post({ type: 'restored', ok, classes: engine ? [...engine.classes] : [] });
        return;
      }
    }
  } catch (err) {
    post({ type: 'failed', message: err instanceof Error ? `${err.message}` : String(err) });
  }
};
