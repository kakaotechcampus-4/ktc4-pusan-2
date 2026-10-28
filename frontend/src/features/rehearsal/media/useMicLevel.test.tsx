// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { MeasureStream } from './level';
import { useMicLevel } from './useMicLevel';

// 오디오 장치는 흉내 냅니다. 분류(measureTrackOf)는 진짜를 써서 훅이 넘기는 값까지 봅니다.
// 측정 트랙은 스트림마다 따로 열리고, 언제 끝날지는 테스트가 정합니다
const pending = new Map<MediaStream, (m: MeasureStream | null) => void>();
vi.mock('./level', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./level')>()),
  openMeasureStream: vi.fn(
    (stream: MediaStream) =>
      new Promise<MeasureStream | null>((resolve) => pending.set(stream, resolve)),
  ),
  // 계량기는 이 테스트의 관심 밖입니다. 없으면 루프가 아무것도 그리지 않습니다
  createLevelMeter: vi.fn(() => Promise.resolve(null)),
}));

function fakeStream(): MediaStream {
  return { getTracks: () => [], getAudioTracks: () => [] } as unknown as MediaStream;
}

/** 측정 트랙을 연 결과를 그 스트림에 돌려줍니다 */
async function settle(stream: MediaStream, measure: MeasureStream | null) {
  await act(async () => pending.get(stream)?.(measure));
}

const unprocessed = (): MeasureStream => ({ stream: fakeStream(), unprocessed: true });

let container: HTMLDivElement;
let root: Root;

/** 훅이 내보낸 값을 그려 둡니다. null 은 'null' 글자로 */
function Probe({ stream }: { stream: MediaStream }) {
  return <>{useMicLevel(stream).measureTrack ?? 'null'}</>;
}

async function render(stream: MediaStream) {
  await act(async () => root.render(<Probe stream={stream} />));
}

/** 지금 훅이 내보내는 measureTrack */
function seen(): string | null {
  const text = container.textContent;
  return text === 'null' ? null : text;
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  pending.clear();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

it('측정 트랙을 열기 전에는 null 이고, 열면 그 결과를 내보낸다', async () => {
  const mic = fakeStream();
  await render(mic);
  expect(seen()).toBeNull();

  await settle(mic, unprocessed());
  expect(seen()).toBe('UNPROCESSED');
});

it('측정 트랙을 못 받으면 FALLBACK 이다', async () => {
  const mic = fakeStream();
  await render(mic);
  await settle(mic, null);
  expect(seen()).toBe('FALLBACK');
});

it('장치를 바꾸면 이전 마이크의 값이 남지 않고, 새 마이크의 값을 기다린다', async () => {
  const before = fakeStream();
  const after = fakeStream();
  await render(before);
  await settle(before, unprocessed());
  expect(seen()).toBe('UNPROCESSED');

  await render(after);
  expect(seen()).toBeNull();

  await settle(after, null);
  expect(seen()).toBe('FALLBACK');
});

it('바꾸기 전 마이크의 결과가 늦게 와도 새 마이크의 값을 덮지 않는다', async () => {
  const before = fakeStream();
  const after = fakeStream();
  await render(before);
  await render(after);
  await settle(after, null);

  await settle(before, unprocessed());
  expect(seen()).toBe('FALLBACK');
});
