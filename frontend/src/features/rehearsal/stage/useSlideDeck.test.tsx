// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useSlideDeck } from './useSlideDeck';
import { useRehearsalStore } from './rehearsalStore';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const elapsedMs = () => 1000;

function Deck({ enabled = true }: { enabled?: boolean }) {
  const { go, slideNumber } = useSlideDeck({
    total: 2,
    clientSessionId: null,
    elapsedMs,
    enabled,
    resumedAt: null,
  });
  return <button onClick={() => go(slideNumber - 1)}>이전</button>;
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
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
});
