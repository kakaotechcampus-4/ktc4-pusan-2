/**
 * Starting the engine: what it needs from the browser, and how it fails.  Every failure is an
 * `EngineInitError` with a reason; the host maps them all to "gaze unavailable" and goes on.
 * MediaPipe itself is replaced here, so these run in Node.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

const { create } = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('../landmarker', () => ({
  createFaceDetector: (...args: unknown[]) => create(...args),
  warmUp: () => undefined,
}));

import { checkSupport, createClassifier, EngineInitError, GazeEngine } from '../index';

const detector = () => ({ detect: vi.fn(), close: vi.fn() });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

afterEach(() => {
  vi.unstubAllGlobals();
  create.mockReset();
});

describe('starting the engine', () => {
  it('requires only what it cannot run without', () => {
    vi.stubGlobal('createImageBitmap', () => undefined);
    expect(checkSupport()).toEqual({ ok: true, missing: [] });
    vi.stubGlobal('createImageBitmap', undefined);
    expect(checkSupport()).toEqual({ ok: false, missing: ['createImageBitmap'] });
  });

  it('refuses an unsupported browser before loading anything', async () => {
    vi.stubGlobal('createImageBitmap', undefined);
    await expect(createClassifier({ assetDir: '/models/' })).rejects.toMatchObject({
      reason: 'UNSUPPORTED_BROWSER',
    });
    expect(create).not.toHaveBeenCalled();
  });

  it('gives up after the timeout and releases a detector that arrives late', async () => {
    vi.stubGlobal('createImageBitmap', () => undefined);
    const late = detector();
    let arrive!: (d: typeof late) => void;
    create.mockReturnValue(new Promise((r) => (arrive = r)));
    const started = createClassifier({ assetDir: '/models/', initTimeoutMs: 10 });
    await expect(started).rejects.toBeInstanceOf(EngineInitError);
    await expect(started).rejects.toMatchObject({ reason: 'TIMEOUT' });
    arrive(late);
    await sleep(0);
    expect(late.close).toHaveBeenCalledTimes(1);
  });

  it('does not throw when a late detector fails to close', async () => {
    vi.stubGlobal('createImageBitmap', () => undefined);
    const late = {
      detect: vi.fn(),
      close: vi.fn(() => {
        throw new Error('closed twice');
      }),
    };
    let arrive!: (d: typeof late) => void;
    create.mockReturnValue(new Promise((r) => (arrive = r)));
    const started = createClassifier({ assetDir: '/models/', initTimeoutMs: 10 });
    await expect(started).rejects.toMatchObject({ reason: 'TIMEOUT' });
    arrive(late);
    await sleep(0);
    expect(late.close).toHaveBeenCalledTimes(1);
  });

  it('reports a failed load with its message', async () => {
    vi.stubGlobal('createImageBitmap', () => undefined);
    create.mockRejectedValue(new Error('404 face_landmarker.task'));
    await expect(createClassifier({ assetDir: '/models/' })).rejects.toMatchObject({
      reason: 'INIT_FAILED',
      message: '404 face_landmarker.task',
    });
  });

  it('starts when the model loads in time', async () => {
    vi.stubGlobal('createImageBitmap', () => undefined);
    const d = detector();
    create.mockResolvedValue(d);
    const engine = await createClassifier({ assetDir: '/models/' });
    expect(engine).toBeInstanceOf(GazeEngine);
    expect(d.close).not.toHaveBeenCalled();
  });
});
