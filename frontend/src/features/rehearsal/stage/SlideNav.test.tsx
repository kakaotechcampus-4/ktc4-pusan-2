// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SlideNav } from './SlideNav';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const render = (slideNumber: number, pageCount: number, enabled: boolean, onGo = vi.fn()) => {
  act(() =>
    root.render(
      <SlideNav slideNumber={slideNumber} pageCount={pageCount} enabled={enabled} onGo={onGo} />,
    ),
  );
  const [prev, next] = Array.from(host.querySelectorAll('button'));
  return { prev: prev!, next: next!, onGo };
};

it('이전 · 다음 버튼이 지금 장 기준으로 한 장씩 넘긴다', () => {
  const { prev, next, onGo } = render(3, 5, true);

  act(() => next.click());
  act(() => prev.click());

  expect(onGo.mock.calls).toEqual([[4], [2]]);
});

it('첫 장에서는 이전을, 마지막 장에서는 다음을 막는다', () => {
  const first = render(1, 5, true);
  expect(first.prev.disabled).toBe(true);
  expect(first.next.disabled).toBe(false);

  const last = render(5, 5, true);
  expect(last.prev.disabled).toBe(false);
  expect(last.next.disabled).toBe(true);
});

it('발표가 돌기 전에는 둘 다 막는다', () => {
  const { prev, next } = render(3, 5, false);
  expect(prev.disabled).toBe(true);
  expect(next.disabled).toBe(true);
});

it('마우스로 눌러도 버튼에 초점을 가져가지 않는다', () => {
  const { next } = render(2, 5, true);
  const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });

  act(() => {
    next.dispatchEvent(down);
  });

  // 기본 동작(초점 이동)을 막아야 그 뒤 Space · ← → 를 슬라이드 키가 받습니다
  expect(down.defaultPrevented).toBe(true);
});
