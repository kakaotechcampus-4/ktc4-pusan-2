// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LAUNCH_AT, useLaunchStatus } from './launch';

let container: HTMLDivElement;
let root: Root;
const launchTime = Date.parse(LAUNCH_AT);

function Probe() {
  const { isOpen } = useLaunchStatus();
  return <p>{isOpen ? 'open' : 'closed'}</p>;
}

async function mount() {
  await act(async () => root.render(<Probe />));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv('VITE_LAUNCH_GATE_ENABLED', 'true');
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it('공개 시각을 지나면 interval 을 정리한다', async () => {
  vi.setSystemTime(launchTime - 1500);
  await mount();
  expect(container.textContent).toBe('closed');
  expect(vi.getTimerCount()).toBe(1);

  await act(async () => vi.advanceTimersByTime(2000));
  expect(container.textContent).toBe('open');
  expect(vi.getTimerCount()).toBe(0);
});

it('이미 공개 시각이 지났으면 interval 을 걸지 않는다', async () => {
  vi.setSystemTime(launchTime + 1000);
  await mount();
  expect(container.textContent).toBe('open');
  expect(vi.getTimerCount()).toBe(0);
});

it('출시 제한이 꺼져 있으면 interval 을 걸지 않는다', async () => {
  vi.stubEnv('VITE_LAUNCH_GATE_ENABLED', 'false');
  vi.setSystemTime(launchTime - 1500);
  await mount();
  expect(container.textContent).toBe('open');
  expect(vi.getTimerCount()).toBe(0);
});
