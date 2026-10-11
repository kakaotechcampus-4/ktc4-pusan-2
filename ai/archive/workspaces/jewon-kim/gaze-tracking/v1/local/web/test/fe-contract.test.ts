/**
 * The frontend contract, exercised with the frontend's own adapter
 * (`frontend/src/workers/aiAdapter.ts`, imported read-only) through the
 * conformance shim -- the exact path `modelClassifier.ts` will take.
 */
import { describe, expect, it } from 'vitest';
import { GazeEngine, type EngineSettings } from '../src/engine/engine';
import { EngineBackedClassifier } from './fe-contract/conformance';
import { FakeDetector, frame, type FakeFrame } from './helpers';

const bitmaps = (frames: FakeFrame[]) => frames as unknown as ImageBitmap[];
const look = (pitch: number, n = 16) =>
  bitmaps(
    Array.from({ length: n }, (_, i) =>
      frame(0.5 + ((i % 3) - 1) * 0.2, pitch + ((i % 5) - 2) * 0.15),
    ),
  );

function classifier(settings: EngineSettings = {}) {
  return new EngineBackedClassifier(
    async () =>
      new GazeEngine<ImageBitmap>(
        {
          detector: new FakeDetector(),
          luma: () => ({ measure: () => ({ face: 120, background: 100 }) }),
        },
        settings,
      ),
  );
}

describe('GazeClassifier (frontend contract) backed by the engine', () => {
  it('stamps the engine version the frontend persists on a Take', async () => {
    const c = classifier();
    expect(c.version).toBe('gaze-module@unloaded');
    await c.init();
    expect(c.version).toBe('gaze_v1.1.0+head_pose+reference_anchor_v1');
  });

  it('runs placement -> calibration -> live frames end to end', async () => {
    const c = classifier();
    await c.init();
    expect(c.checkPlacement(look(18), look(11))).toEqual({
      placement: 'TOP',
      supported: true,
      reason: 'OK',
    });

    const result = c.fitCalibration(look(18), look(4));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.ref.quality).toBe('GOOD');
    expect(result.advice).toBeNull();
    expect(result.ref.metrics!.looAccuracy).toBe(1);

    // The frontend keeps the ref in IndexedDB: structured clone, then calibrate().
    c.calibrate(structuredClone(result.ref));
    expect(c.classify(look(18, 1)[0]!, 10_000)).toEqual({
      zone: 'CAMERA',
      confidence: expect.any(Number),
    });
    expect(c.classify(look(11, 1)[0]!, 10_125)!.zone).toBe('BOTTOM');
    expect(c.classify(look(4, 1)[0]!, 10_250)!.zone).toBe('BOTTOM');
    // No face -> null (dropped from the sample, not a vote).
    expect(c.classify(bitmaps([frame(0, 0, { empty: true })])[0]!, 10_375)).toBeNull();
  });

  it('returns null before a reference is applied', async () => {
    const c = classifier();
    await c.init();
    expect(c.classify(look(18, 1)[0]!, 1)).toBeNull();
  });

  it('turns an unusable calibration into the frontend retry path', async () => {
    const c = classifier();
    await c.init();
    // Lens and script at the same posture: a model exists but cannot separate them.
    const result = c.fitCalibration(look(18), look(17.6));
    expect(result).toMatchObject({
      ok: true,
      ref: { quality: 'POOR' },
      advice: 'CLASS_NOT_SEPARABLE',
    });
  });

  it('maps engine-only reasons the frontend does not list to ENGINE_ERROR advice (known gap)', async () => {
    // A script region diluted until its own anchor is not decided as BOTTOM: the engine
    // says ANCHOR_AMBIGUOUS, which frontend CalibrationFailReason does not list, so the
    // adapter turns it into ENGINE_ERROR -- see web/README.md, "FE 연동 시 확인할 것".
    const c = classifier({
      config: {
        calibration: {
          script_width_fraction: 1,
          script_height_fraction: 1,
          prior_bottom: 0.05,
          merge_inseparable_screen: false,
        },
      },
    });
    await c.init();
    c.checkPlacement(look(18), look(11));
    const result = c.fitCalibration(look(18), look(4));
    expect(result).toMatchObject({ ok: true, ref: { quality: 'POOR' }, advice: 'ENGINE_ERROR' });
  });
});
