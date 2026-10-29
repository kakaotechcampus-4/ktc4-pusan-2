// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { END_CONFIRM_MS, useEndConfirm } from './useEndConfirm';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

function Probe({ slide }: { slide: number }) {
  const { confirming, ask } = useEndConfirm(slide);
  return <button onClick={ask}>{confirming ? 'yes' : 'no'}</button>;
}

const text = () => host.textContent;
const press = () => act(() => host.querySelector('button')!.click());
const render = (slide: number) => act(() => root.render(<Probe slide={slide} />));
const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms));

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('누르면 켜지고 시간이 지나면 꺼진다', () => {
  render(5);
  press();
  expect(text()).toBe('yes');
  wait(END_CONFIRM_MS);
  expect(text()).toBe('no');
});

it('장이 바뀌면 꺼지고, 시간 안에 돌아와도 다시 묻는다', () => {
  render(5);
  press();
  render(4);
  expect(text()).toBe('no');
  wait(1000);
  render(5);
  expect(text()).toBe('no');
});

it('다시 누르면 이전 타이머가 새 확인을 일찍 풀지 않는다', () => {
  render(5);
  press();
  wait(END_CONFIRM_MS - 500);
  press();
  wait(1000);
  expect(text()).toBe('yes');
});
