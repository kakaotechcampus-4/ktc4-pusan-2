// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PARSE_WAIT_TEXT } from '../lib/parseWait';
import { ParseWaitHint } from './ParseWaitHint';

let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const status = () => host.querySelector('[role="status"]')!.textContent;

it('기다린 시간에 따라 안내가 바뀐다 — 3초 전 없음, 3초부터 보통, 10초부터 오래 걸림', async () => {
  await act(async () => root.render(<ParseWaitHint startedAt={performance.now()} />));
  expect(status()).toBe('');

  await act(async () => vi.advanceTimersByTime(3_000));
  expect(status()).toBe(PARSE_WAIT_TEXT.normal);

  await act(async () => vi.advanceTimersByTime(7_000));
  expect(status()).toBe(PARSE_WAIT_TEXT.slow);
});

it('다른 화면에 다녀와 다시 그려도 시작 시각부터 이어서 센다', async () => {
  const startedAt = performance.now();
  await act(async () => vi.advanceTimersByTime(12_000));
  await act(async () => root.render(<ParseWaitHint startedAt={startedAt} />));
  expect(status()).toBe(PARSE_WAIT_TEXT.slow);
});
