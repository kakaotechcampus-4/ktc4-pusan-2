/**
 * The frontend's own gaze code, run against this engine -- what the frontend will run once it
 * copies `src/engine` (and the worker, the camera view) to `frontend/src/vendor/gaze/`.
 *
 * `@/vendor/gaze/*` resolves to this folder's `src/*`; every other `@/` and `@fe/` import is the
 * frontend's own `src`, read-only (vite.config.ts, tsconfig.json).  So the classifier below is
 * the real `frontend/src/workers/modelClassifier.ts` with its adapter (`aiAdapter.ts`) and engine
 * check (`calibrationModel.ts`), and the 1 s records come out of the engine's `GazeSlicer` the
 * way the frontend's worker makes them (`frontend/src/workers/gaze.worker.ts`).
 *
 * Only `createClassifier` is replaced: it would load MediaPipe, so the engine is built around a
 * fake face detector instead.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelGazeClassifier } from '@fe/workers/modelClassifier';
import {
  GazeSlicer,
  makeConfig,
  sampleToDict,
  type GazeSample,
} from '@/vendor/gaze/engine/contract';
import type { EngineSettings } from '../src/engine/engine';
import { frame, type FakeFrame } from '../src/engine/__tests__/helpers';

const engine = vi.hoisted(() => ({ settings: {} as EngineSettings, calls: [] as unknown[] }));

vi.mock('@/vendor/gaze/engine', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/engine')>();
  const { GazeEngine } = await import('../src/engine/engine');
  const { FakeDetector } = await import('../src/engine/__tests__/helpers');
  return {
    ...actual,
    // What the frontend asks for is recorded (asset folder ...); the engine itself is built
    // around the fake detector with the test's settings.
    createClassifier: async (opts: unknown) =>
      engine.calls.push(opts) &&
      new GazeEngine<ImageBitmap>(
        {
          detector: new FakeDetector(),
          luma: () => ({ measure: () => ({ face: 120, background: 100 }) }),
        },
        engine.settings,
      ),
  };
});

const bitmaps = (frames: FakeFrame[]) => frames as unknown as ImageBitmap[];
const look = (pitch: number, n = 16) =>
  bitmaps(
    Array.from({ length: n }, (_, i) =>
      frame(0.5 + ((i % 3) - 1) * 0.2, pitch + ((i % 5) - 2) * 0.15),
    ),
  );

async function calibrated() {
  const c = new ModelGazeClassifier();
  await c.init();
  c.checkPlacement(look(18), look(11));
  const result = c.fitCalibration(look(18), look(4));
  if (!result.ok) throw new Error(`calibration failed: ${result.reason}`);
  return { c, result };
}

/** The frontend worker's loop: one frame every 125 ms from Take time 0, then `flush` at the end. */
function live(c: ModelGazeClassifier, pitches: number[]): GazeSample[] {
  const slicer = new GazeSlicer(makeConfig().evidence);
  const records: GazeSample[] = [];
  pitches.forEach((pitch, i) => {
    const f = c.classify(look(pitch, 1)[0]!, i * 125);
    expect(f).not.toBeNull();
    records.push(...slicer.push(f!));
  });
  records.push(...slicer.flush(pitches.length * 125));
  return records;
}

beforeEach(() => {
  engine.settings = {};
  engine.calls = [];
});

describe('frontend gaze code (modelClassifier + aiAdapter) on this engine', () => {
  it('stamps the engine version the frontend sends with the calibration result', async () => {
    const c = new ModelGazeClassifier();
    expect(c.version).toBe('gaze-module@unloaded');
    await c.init();
    expect(c.version).toBe('gaze_v1.1.0+head_pose+reference_anchor_v1');
    // Assets from the frontend's own server, no CDN (doc 11: public/models/).
    expect(engine.calls).toEqual([{ assetDir: '/models/' }]);
  });

  it('runs placement -> calibration -> live frames -> 1 s records', async () => {
    const c = new ModelGazeClassifier();
    await c.init();
    expect(c.checkPlacement(look(18), look(11))).toEqual({
      placement: 'TOP',
      supported: true,
      reason: 'OK',
    });
    const result = c.fitCalibration(look(18), look(4));
    expect(result).toMatchObject({ ok: true, ref: { quality: 'GOOD' }, advice: null });
    if (!result.ok) return;
    expect(result.ref.metrics!.looAccuracy).toBe(1);

    // The device check keeps the calibration in tab memory; the stage makes its own worker and
    // engine and gets a structured clone of it (doc 5-6 · 12-1).
    const stage = new ModelGazeClassifier();
    await stage.init();
    stage.calibrate(structuredClone(result.ref));

    // The frontend worker: Take-relative frame times, each frame into GazeSlicer, the slices
    // out; the last partial slice is closed at the Take's end (flush).
    // Lens, screen centre, script: the record keeps the screen apart from the script.
    const looks = [18, 11, 4].flatMap((pitch) => Array<number>(8).fill(pitch));
    const records = live(stage, looks);
    // The grid starts at the engine's first frame (doc 5-2: not always a multiple of 1000).
    const t0 = records[0]!.t_ms;
    expect(t0).toBeLessThan(125);
    expect(records.map((r) => [r.t_ms - t0, r.state])).toEqual([
      [0, 'CAMERA'],
      [1000, 'SCREEN'],
      [2000, 'BOTTOM'],
    ]);
    expect(records.slice(0, 2).map((r) => r.duration_ms)).toEqual([1000, 1000]);
    // The last slice ends where the Take ends (flush at the Take length, 3000 ms).
    expect(records[2]!.t_ms + records[2]!.duration_ms).toBe(3000);
    // The wire record: the 8 fields of doc 5-2 (architecture.md 6-2), nothing face-derived.
    expect(Object.keys(sampleToDict(records[0]!)).sort()).toEqual([
      'confidence',
      'direction',
      'duration_ms',
      'frames',
      'issues',
      'reliability',
      'state',
      't_ms',
    ]);
  });

  it('shifts Take time if the calibrating engine is reused (why the stage makes a new one)', async () => {
    const { c, result } = await calibrated();
    c.calibrate(result.ref);
    // The engine never moves its clock back: Take time 0 lands after the calibration frames.
    const [first] = live(c, Array<number>(8).fill(18));
    expect(first!.t_ms).toBeGreaterThan(0);
  });

  it('a frame without a face is an UNMEASURED frame, not a dropped one', async () => {
    const { c, result } = await calibrated();
    c.calibrate(result.ref);
    expect(c.classify(bitmaps([frame(0, 0, { empty: true })])[0]!, 0)).toMatchObject({
      state: 'UNMEASURED',
    });
  });

  it('judges nothing before a calibration is applied', async () => {
    const c = new ModelGazeClassifier();
    await c.init();
    expect(c.classify(look(18, 1)[0]!, 1)).toBeNull();
  });

  it('refuses a calibration made with another engine config (frontend config-hash check)', async () => {
    const { c, result } = await calibrated();
    const stale = structuredClone(result.ref);
    (stale.model as { configHash: string }).configHash = 'another-config';
    c.calibrate(stale);
    expect(c.classify(look(18, 1)[0]!, 0)).toBeNull();
  });

  it('keeps a calibration that failed the check as POOR with advice (the presentation goes on)', async () => {
    const c = new ModelGazeClassifier();
    await c.init();
    // Lens and script at the same posture: a model exists but cannot separate them.
    expect(c.fitCalibration(look(18), look(17.6))).toMatchObject({
      ok: true,
      ref: { quality: 'POOR' },
      advice: 'CLASS_NOT_SEPARABLE',
    });
  });

  it('passes ANCHOR_AMBIGUOUS through as a calibration reason the frontend lists', async () => {
    // A script region diluted until its own anchor is not decided as BOTTOM.
    engine.settings = {
      config: {
        calibration: {
          script_width_fraction: 1,
          script_height_fraction: 1,
          prior_bottom: 0.05,
          merge_inseparable_screen: false,
        },
      },
    };
    const c = new ModelGazeClassifier();
    await c.init();
    c.checkPlacement(look(18), look(11));
    expect(c.fitCalibration(look(18), look(4))).toMatchObject({
      ok: true,
      ref: { quality: 'POOR' },
      advice: 'ANCHOR_AMBIGUOUS',
    });
  });
});
