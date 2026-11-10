// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SlideClickArea } from './SlideClickArea';

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

const render = (slideNumber: number, enabled: boolean) => {
  const onGo = vi.fn();
  act(() =>
    root.render(<SlideClickArea slideNumber={slideNumber} enabled={enabled} onGo={onGo} />),
  );
  return { area: host.querySelector('button')!, onGo };
};

const rightClick = (target: Element) => {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
};

it('좌클릭은 다음 장, 우클릭은 이전 장으로 넘긴다', () => {
  const { area, onGo } = render(3, true);

  act(() => area.click());
  const menu = rightClick(area);

  expect(onGo.mock.calls).toEqual([[4], [2]]);
  // 발표 중에는 브라우저 메뉴 대신 이전 장으로 갑니다
  expect(menu.defaultPrevented).toBe(true);
});

it('발표가 돌기 전에는 넘기지 않고 브라우저 메뉴도 막지 않는다', () => {
  const { area, onGo } = render(3, false);

  act(() => area.click());
  const menu = rightClick(area);

  expect(onGo).not.toHaveBeenCalled();
  expect(menu.defaultPrevented).toBe(false);
});

it('막아 두지 않아 마지막 장에서도 우클릭으로 돌아갈 수 있다', () => {
  // 처음 · 끝에서 멈추는 건 go 가 합니다. 여기서 disabled 면 우클릭 이벤트가 오지 않습니다
  const { area, onGo } = render(12, true);

  expect(area.disabled).toBe(false);
  rightClick(area);
  expect(onGo).toHaveBeenCalledWith(11);
});

it('마우스로 눌러도 초점을 가져가지 않는다', () => {
  const { area } = render(2, true);
  const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });

  act(() => {
    area.dispatchEvent(down);
  });

  // 기본 동작(초점 이동)을 막아야 그 뒤 Space · ← → 를 슬라이드 키가 받습니다
  expect(down.defaultPrevented).toBe(true);
});
