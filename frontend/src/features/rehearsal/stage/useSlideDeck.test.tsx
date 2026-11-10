// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { appendSlideChange } from '../lib/db';
import { useSlideDeck } from './useSlideDeck';
import { useRehearsalStore } from './rehearsalStore';

vi.mock('../lib/db', () => ({
  appendSlideChange: vi.fn().mockResolvedValue(undefined),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const elapsedMs = () => 1000;

function Deck({
  enabled = true,
  total = 2,
  clientSessionId = null,
}: {
  enabled?: boolean;
  total?: number;
  clientSessionId?: string | null;
}) {
  const { go, slideNumber } = useSlideDeck({
    total,
    clientSessionId,
    elapsedMs,
    enabled,
    resumedAt: null,
  });
  return (
    <>
      <button onClick={() => go(slideNumber - 1)}>이전</button>
      <button
        type="button"
        className="slide-advance"
        aria-label="다음 슬라이드로 이동"
        disabled={!enabled || slideNumber >= total}
        onClick={() => go(slideNumber + 1)}
      />
    </>
  );
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.clearAllMocks();
  useRehearsalStore.getState().reset();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('clamps keyboard navigation and leaves Space available to a focused button', async () => {
  await act(async () => root.render(<Deck />));
  for (const key of ['ArrowRight', 'ArrowRight']) {
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key }));
    });
  }
  expect(useRehearsalStore.getState().slideNumber).toBe(2);
  const button = host.querySelector('button')!;
  button.focus();
  const space = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
  await act(async () => {
    button.dispatchEvent(space);
  });
  expect(space.defaultPrevented).toBe(false);
  await act(async () => button.click());
  expect(useRehearsalStore.getState().slideNumber).toBe(1);
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  });
  expect(useRehearsalStore.getState().slideNumber).toBe(1);
});

it('does not navigate before starting or after ending', async () => {
  useRehearsalStore.getState().setSlide(2);
  await act(async () => root.render(<Deck enabled={false} />));
  await act(async () => host.querySelector('button')!.click());
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  });
  expect(useRehearsalStore.getState().slideNumber).toBe(2);
  const nextButton = host.querySelector<HTMLButtonElement>('.slide-advance')!;
  expect(nextButton.disabled).toBe(true);
  await act(async () => nextButton.click());
  expect(useRehearsalStore.getState().slideNumber).toBe(2);
});

it('advances by slide click and records each transition once', async () => {
  await act(async () => root.render(<Deck total={3} clientSessionId="test-session" />));
  const nextButton = host.querySelector<HTMLButtonElement>('.slide-advance')!;

  // 한 렌더에서 연속 클릭해도 같은 장을 두 번 기록하지 않습니다.
  await act(async () => {
    nextButton.click();
    nextButton.click();
  });
  expect(useRehearsalStore.getState().slideNumber).toBe(2);
  expect(appendSlideChange).toHaveBeenCalledWith('test-session', 0, 1);
  expect(appendSlideChange).toHaveBeenCalledWith('test-session', 1000, 2);
  expect(appendSlideChange).toHaveBeenCalledTimes(2);

  await act(async () => nextButton.click());
  expect(useRehearsalStore.getState().slideNumber).toBe(3);
  expect(appendSlideChange).toHaveBeenCalledWith('test-session', 1000, 3);
  expect(appendSlideChange).toHaveBeenCalledTimes(3);
  expect(nextButton.disabled).toBe(true);

  await act(async () => nextButton.click());
  expect(useRehearsalStore.getState().slideNumber).toBe(3);
  expect(appendSlideChange).toHaveBeenCalledTimes(3);
});
