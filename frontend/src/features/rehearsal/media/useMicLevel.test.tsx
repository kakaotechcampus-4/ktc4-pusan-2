// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AudioInputMeter } from '../prepare/AudioInputMeter';
import { useMicLevel } from './useMicLevel';

const fixture = vi.hoisted(() => ({
  db: -30,
  state: 'running' as AudioContextState,
  open: vi.fn(),
  create: vi.fn(),
  stop: vi.fn(),
  read: vi.fn(),
  render: vi.fn(),
}));
vi.mock('./level', () => ({
  openMeasureStream: fixture.open,
  createLevelMeter: fixture.create,
}));

const first = { getAudioTracks: () => [{ readyState: 'live' }] } as unknown as MediaStream;
const second = { getAudioTracks: () => [{ readyState: 'live' }] } as unknown as MediaStream;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let frame: FrameRequestCallback | undefined;
let frameId = 0;
const cancelFrame = vi.fn();

function Harness({
  stream,
  kind,
}: {
  stream: MediaStream | null;
  kind: 'input' | 'clip' | 'width';
}) {
  const { meterRef, echoMeterRef, rowRef, meterError } = useMicLevel(stream);
  fixture.render();
  return (
    <>
      {kind === 'input' ? (
        <AudioInputMeter meterRef={meterRef} />
      ) : (
        <div className="legacy-meter" ref={meterRef} data-meter={kind} />
      )}
      <div className="echo-meter" ref={echoMeterRef} data-meter="clip" />
      <span ref={rowRef} />
      {meterError && <p role="alert">{meterError}</p>}
    </>
  );
}
async function render(
  stream: MediaStream | null = first,
  kind: 'input' | 'clip' | 'width' = 'input',
) {
  await act(async () => root.render(<Harness stream={stream} kind={kind} />));
}
async function tick() {
  expect(frame).toBeDefined();
  await act(async () => frame!(performance.now()));
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  frame = undefined;
  frameId = 0;
  cancelFrame.mockReset();
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => {
    frame = next;
    return ++frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', cancelFrame);
  fixture.db = -30;
  fixture.state = 'running';
  fixture.open.mockReset().mockResolvedValue(null);
  fixture.stop.mockReset();
  fixture.read.mockReset().mockReturnValue(0.5);
  fixture.render.mockReset();
  fixture.create.mockReset().mockResolvedValue({
    get state() {
      return fixture.state;
    },
    read: fixture.read,
    levelDb: () => fixture.db,
    speech: () => ({ leqDb: null, ms: 0 }),
    speaking: () => false,
    silentMs: () => 0,
    onStateChange: vi.fn(),
    stop: fixture.stop,
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('keeps the input meter empty without opening a meter before a stream exists', async () => {
  await render(null);
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('0');
  expect(fixture.open).not.toHaveBeenCalled();
  expect(fixture.create).not.toHaveBeenCalled();
});

it('paints the actual input scale, clamps bounds and updates accessibility without repeated React renders', async () => {
  await render();
  await tick();
  const renders = fixture.render.mock.calls.length;
  const meter = host.querySelector<HTMLElement>('[role="meter"]')!;
  for (const [db, percent] of [
    [-100, 0],
    [-60, 0],
    [-30, 50],
    [-6, 90],
    [10, 100],
    [-30, 50],
  ] as const) {
    fixture.db = db;
    await tick();
    expect(parseFloat(meter.style.getPropertyValue('--prepare-input-level'))).toBe(percent);
    expect(meter.getAttribute('aria-valuenow')).toBe(String(percent));
  }
  expect(fixture.render).toHaveBeenCalledTimes(renders);
  expect(meter.querySelectorAll('.prepare-input-fill > span')).toHaveLength(48);
  expect(meter.querySelectorAll('[data-band="green"]')).toHaveLength(32);
  expect(meter.querySelectorAll('[data-band="yellow"]')).toHaveLength(10);
  expect(meter.querySelectorAll('[data-band="red"]')).toHaveLength(6);
  expect(meter.textContent).toBe('');
  expect(meter.querySelector('input,button')).toBeNull();
  expect(host.querySelector<HTMLElement>('.echo-meter')!.style.clipPath).toContain('50.0%');
});

it('clears stale input on disconnection and device replacement and stops the previous meter', async () => {
  await render();
  await tick();
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('50');
  await render(second);
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('0');
  expect(fixture.stop).toHaveBeenCalledOnce();
  await tick();
  await render(null);
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('0');
  expect(fixture.stop).toHaveBeenCalledTimes(2);
  expect(cancelFrame).toHaveBeenCalled();
});

it('shows zero while audio is suspended and resumes the live level afterward', async () => {
  await render();
  await tick();
  fixture.state = 'suspended';
  await tick();
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('0');
  fixture.state = 'running';
  await tick();
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('50');
});

it.each(['clip', 'width'] as const)(
  'keeps the existing %s meter renderer working',
  async (kind) => {
    await render(first, kind);
    await tick();
    const meter = host.querySelector<HTMLElement>('.legacy-meter')!;
    if (kind === 'clip') expect(meter.style.clipPath).toContain('50.0%');
    else expect(parseFloat(meter.style.width)).toBe(50);
  },
);

it('releases a measurement stream that arrives after the page has unmounted', async () => {
  let resolve!: (value: unknown) => void;
  fixture.open.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const stop = vi.fn();
  await render();
  await act(async () => root.render(null));
  await act(async () => resolve({ stream: { getTracks: () => [{ stop }] } }));
  expect(stop).toHaveBeenCalledOnce();
  expect(fixture.create).not.toHaveBeenCalled();
});

it('keeps zero input and the existing error guidance when meter initialization fails', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  fixture.create.mockRejectedValueOnce(new Error('device lost'));
  await render();
  expect(host.querySelector('[role="meter"]')!.getAttribute('aria-valuenow')).toBe('0');
  expect(host.querySelector('[role="alert"]')!.textContent).toContain(
    '마이크 음량을 측정하지 못했어요',
  );
});
