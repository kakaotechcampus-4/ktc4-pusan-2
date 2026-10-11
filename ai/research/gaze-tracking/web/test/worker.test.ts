/**
 * The camera view's worker: every frame message gets exactly one answer (the main thread's
 * back-pressure signal), also when the engine throws on that frame, and a failed start says why.
 * The engine is replaced and `self` is a stub, so this runs in Node.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EngineInitError } from '../src/engine';

const { create } = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('../src/engine', async (importActual) => ({
  ...(await importActual<typeof import('../src/engine')>()),
  createClassifier: (...args: unknown[]) => create(...args),
}));

type Handler = (event: { data: unknown }) => Promise<void>;
const posted: Array<Record<string, unknown>> = [];
const scope = {
  onmessage: null as Handler | null,
  crossOriginIsolated: false,
  postMessage: (msg: Record<string, unknown>) => posted.push(msg),
};
vi.stubGlobal('self', scope);
await import('../src/worker/gaze.worker');
const send = (data: unknown) => scope.onmessage!({ data });
const bitmap = () => ({ width: 640, height: 480, close: vi.fn() });

const observation = {
  tMs: 0,
  faceValid: true,
  invalidReason: null,
  guide: null,
  headPose: { yaw: 0, pitch: 0, roll: 0 },
  imageSize: [640, 480],
};

beforeEach(() => {
  posted.length = 0;
  create.mockReset();
});

describe('gaze worker', () => {
  it('says why the engine did not start', async () => {
    create.mockRejectedValue(new EngineInitError('TIMEOUT', 'model and runtime did not load'));
    await send({ type: 'init', assetDir: '/models/' });
    expect(posted).toEqual([
      { type: 'failed', message: 'model and runtime did not load', reason: 'TIMEOUT' },
    ]);
  });

  it('answers a frame the engine throws on, and reports the error once per streak', async () => {
    let broken = true;
    create.mockResolvedValue({
      version: { modelVersion: 'm', gazeBackbone: 'b', gazeClassifier: 'c' },
      lastTiming: { detectMs: 1 },
      observe: () => {
        if (broken) throw new Error('detector failed');
        return observation;
      },
    });
    await send({ type: 'init', assetDir: '/models/' });
    posted.length = 0;

    const first = bitmap();
    await send({ type: 'frame', bitmap: first, tMs: 125, mode: 'preview' });
    await send({ type: 'frame', bitmap: bitmap(), tMs: 250, mode: 'preview' });
    expect(posted.map((m) => m.type)).toEqual(['frame', 'failed', 'frame']);
    expect(posted[0]).toMatchObject({
      frame: { tMs: 125, faceValid: false, imageSize: [640, 480] },
    });
    expect(posted[1]).toMatchObject({ message: 'detector failed', reason: 'FRAME_FAILED' });
    expect(first.close).toHaveBeenCalledTimes(1);

    broken = false;
    posted.length = 0;
    await send({ type: 'frame', bitmap: bitmap(), tMs: 375, mode: 'preview' });
    broken = true;
    await send({ type: 'frame', bitmap: bitmap(), tMs: 500, mode: 'preview' });
    expect(posted.map((m) => m.type)).toEqual(['frame', 'frame', 'failed']);
    expect(posted[0]).toMatchObject({ frame: { faceValid: true } });
  });
});
