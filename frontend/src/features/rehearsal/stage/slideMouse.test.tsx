// @vitest-environment jsdom
/**
 * 마우스로 넘기기(SlideClickArea · NextSlideButton)를 실제 useSlideDeck.go 에 붙여 확인합니다 (#1192 리뷰).
 * 장 넘김 기록은 IndexedDB 사본(`appendSlideChange`)으로 봅니다 — WS `slide` 와 1초 기록의 장 번호는
 * 같은 스토어 · 타임라인을 읽으므로(useSlideSender · slideAt) 입력 방법과 상관없이 같습니다.
 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NextSlideButton } from './NextSlideButton';
import { useRehearsalStore } from './rehearsalStore';
import { SlideClickArea } from './SlideClickArea';
import { useSlideDeck } from './useSlideDeck';

const appendSlideChange = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock('../lib/db', () => ({ appendSlideChange }));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let now = 0;
const elapsedMs = () => now;
const onEnd = vi.fn();

function Stage({ enabled = true, total = 3 }: { enabled?: boolean; total?: number }) {
  const { slideNumber, go } = useSlideDeck({
    total,
    clientSessionId: 's1',
    elapsedMs,
    enabled,
    resumedAt: null,
  });
  return (
    <>
      <div className="slide">
        <SlideClickArea slideNumber={slideNumber} enabled={enabled} onGo={go} />
      </div>
      {slideNumber < total && (
        <NextSlideButton enabled={enabled} onNext={() => go(slideNumber + 1)}>
          SLIDE {slideNumber + 1}
        </NextSlideButton>
      )}
      <button type="button" className="end" onClick={onEnd}>
        발표 끝내기
      </button>
    </>
  );
}

const area = () => host.querySelector<HTMLButtonElement>('.slide-advance')!;
const slide = () => useRehearsalStore.getState().slideNumber;
/** 기록된 장 번호들. 첫 행은 무대가 열릴 때의 0ms 행입니다 */
const recorded = () => appendSlideChange.mock.calls.map((call) => (call as unknown[])[2]);

/** 브라우저처럼 mousedown → click 순서로 누릅니다 (초점 처리가 mousedown 에 있습니다) */
async function leftClick(target: HTMLElement) {
  await act(async () => {
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    target.click();
  });
}
async function rightClick(target: HTMLElement) {
  await act(async () => {
    target.dispatchEvent(
      new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 2 }),
    );
    target.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  });
}
async function press(key: string) {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, cancelable: true }));
  });
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  useRehearsalStore.getState().reset();
  appendSlideChange.mockClear();
  onEnd.mockClear();
  now = 0;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('좌클릭 · 우클릭 · 다음 장 칸이 키보드와 같은 기록 경로로 장을 넘긴다', async () => {
  await act(async () => root.render(<Stage />));
  // 첫 장 0ms 행
  expect(appendSlideChange).toHaveBeenLastCalledWith('s1', 0, 1);

  now = 2000;
  await leftClick(area());
  expect(slide()).toBe(2);
  expect(appendSlideChange).toHaveBeenLastCalledWith('s1', 2000, 2);

  now = 3000;
  await rightClick(area());
  expect(slide()).toBe(1);
  expect(appendSlideChange).toHaveBeenLastCalledWith('s1', 3000, 1);

  now = 4000;
  await leftClick(host.querySelector<HTMLButtonElement>('.next-go')!);
  expect(slide()).toBe(2);
  expect(appendSlideChange).toHaveBeenLastCalledWith('s1', 4000, 2);
  expect(recorded()).toEqual([1, 2, 1, 2]);
});

it('빠르게 여러 번 눌러도 장이 바뀐 만큼만 기록하고 마지막 장에서 멈춘다', async () => {
  await act(async () => root.render(<Stage />));
  for (let i = 0; i < 5; i++) {
    now += 100;
    await leftClick(area());
  }
  expect(slide()).toBe(3);
  // 0ms 행 + 2 · 3. 마지막 장에서 더 누른 세 번은 기록하지 않습니다
  expect(recorded()).toEqual([1, 2, 3]);

  // 첫 장에서 더 뒤로 가도 기록이 늘지 않습니다
  for (let i = 0; i < 4; i++) await rightClick(area());
  expect(slide()).toBe(1);
  expect(recorded()).toEqual([1, 2, 3, 2, 1]);
});

it('마지막 장에서도 우클릭으로 이전 장에 돌아간다', async () => {
  await act(async () => root.render(<Stage />));
  await press('ArrowRight');
  await press('ArrowRight');
  expect(slide()).toBe(3);

  await rightClick(area());
  expect(slide()).toBe(2);
});

it('발표가 돌기 전에는 마우스로 넘기지 않고 기록도 하지 않는다', async () => {
  await act(async () => root.render(<Stage enabled={false} />));
  await leftClick(area());
  await rightClick(area());
  expect(slide()).toBe(1);
  expect(appendSlideChange).not.toHaveBeenCalled();
});

it('Tab 으로 다른 버튼에 둔 초점을 마우스로 넘기면 풀어, 그 뒤 Space · ← → 가 장을 넘긴다', async () => {
  await act(async () => root.render(<Stage />));
  const end = host.querySelector<HTMLButtonElement>('.end')!;
  end.focus();
  expect(document.activeElement).toBe(end);

  await leftClick(area());
  // 초점이 [발표 끝내기]에 남아 있으면 브라우저는 그 뒤 Space 로 그 버튼을 누릅니다
  expect(document.activeElement).not.toBe(end);
  expect(document.activeElement).not.toBe(area());

  await press(' ');
  expect(slide()).toBe(3);
  await press('ArrowLeft');
  expect(slide()).toBe(2);
  expect(onEnd).not.toHaveBeenCalled();

  // 우클릭 · 다음 장 칸도 같습니다
  end.focus();
  await rightClick(area());
  expect(document.activeElement).not.toBe(end);
  end.focus();
  await leftClick(host.querySelector<HTMLButtonElement>('.next-go')!);
  expect(document.activeElement).not.toBe(end);
  expect(slide()).toBe(2);
});

it('마우스를 쓰지 않으면 Tab 으로 옮긴 초점은 그대로다', async () => {
  await act(async () => root.render(<Stage />));
  const end = host.querySelector<HTMLButtonElement>('.end')!;
  end.focus();
  await press('ArrowRight');
  expect(slide()).toBe(2);
  expect(document.activeElement).toBe(end);
});
