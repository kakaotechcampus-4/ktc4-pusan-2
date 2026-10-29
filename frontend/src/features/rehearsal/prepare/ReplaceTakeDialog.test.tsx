// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ActiveTake } from '@/types/take';
import { ReplaceTakeDialog } from './ReplaceTakeDialog';

const other: ActiveTake = {
  take_id: 't-live',
  pitch_id: 'p-other',
  pitch_title: '캡스톤 최종 발표',
  take_number: 3,
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  // jsdom 에는 showModal · close 가 없습니다 — 열림 표시만 흉내 냅니다
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render(busy: boolean, onConfirm = vi.fn(), onCancel = vi.fn()) {
  await act(async () =>
    root.render(
      <ReplaceTakeDialog take={other} busy={busy} onConfirm={onConfirm} onCancel={onCancel} />,
    ),
  );
  return { onConfirm, onCancel };
}
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === text)!;
const pressEscape = () =>
  act(async () => {
    host.querySelector('dialog')!.dispatchEvent(new Event('cancel', { cancelable: true }));
  });

it('어느 발표의 몇 번째 연습인지 알리고, 끝낸 연습은 리포트가 없다고 말한다', async () => {
  await render(false);
  const text = host.querySelector('dialog')!.textContent;
  expect(text).toContain('캡스톤 최종 발표');
  expect(text).toContain('3번째 연습');
  expect(text).toContain('리포트가 만들어지지 않아요');
});

it('끝내고 시작 · 취소 · Esc 가 각각 알린다', async () => {
  const { onConfirm, onCancel } = await render(false);
  await act(async () => button('그 연습 끝내고 시작').click());
  expect(onConfirm).toHaveBeenCalledOnce();
  await act(async () => button('취소').click());
  await pressEscape();
  expect(onCancel).toHaveBeenCalledTimes(2);
});

/** 브라우저는 Esc 를 연달아 받으면 preventDefault 를 무시하고 창을 닫습니다 */
it('브라우저가 창을 닫아도 끝내는 중이면 다시 띄우고, 아니면 취소로 본다', async () => {
  const closeByBrowser = () =>
    act(async () => {
      const dialog = host.querySelector('dialog')!;
      dialog.open = false;
      dialog.dispatchEvent(new Event('close'));
    });

  const busy = await render(true);
  await closeByBrowser();
  expect(host.querySelector('dialog')!.open).toBe(true);
  expect(busy.onCancel).not.toHaveBeenCalled();

  const idle = await render(false);
  await closeByBrowser();
  expect(idle.onCancel).toHaveBeenCalledOnce();
});

it('끝내는 중에는 버튼도 Esc 도 막는다', async () => {
  const { onConfirm, onCancel } = await render(true);
  expect(button('끝내는 중…').disabled).toBe(true);
  expect(button('취소').disabled).toBe(true);
  await pressEscape();
  expect(onConfirm).not.toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
});
