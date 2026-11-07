// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { SlideVersion } from './lib/draft';
import { Pages } from './steps/SlidePane';

vi.mock('@/shared/ui/PdfPage', () => ({ PdfPage: () => null }));

const doc = { numPages: 3 } as PDFDocumentProxy;
const slide = { version: 1, fileName: 'deck.pdf' } as SlideVersion;
let container: HTMLDivElement;
let root: Root;
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Pages slide={slide} doc={doc} />));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const button = (label: string) =>
  container.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;
const counter = () => container.querySelector('[aria-live="polite"]')!.textContent;
const click = (el: HTMLElement) => act(async () => el.click());
const press = (el: HTMLElement, key: string) =>
  act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });

it('마우스로 끝 장까지 가도 다음 버튼이 포커스를 쥐고, 거기서 ←가 동작한다 (#818)', async () => {
  const next = button('다음 슬라이드');
  next.focus();
  await click(next);
  await click(next);
  expect(counter()).toBe('3 / 3');
  expect(next.disabled).toBe(false);
  expect(next.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(next);

  await click(next);
  expect(counter()).toBe('3 / 3');

  await press(next, 'ArrowLeft');
  expect(counter()).toBe('2 / 3');
});

it('첫 장의 이전 버튼도 같다', async () => {
  const prev = button('이전 슬라이드');
  prev.focus();
  await click(prev);
  expect(counter()).toBe('1 / 3');
  expect(prev.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(prev);

  await press(prev, 'ArrowRight');
  expect(counter()).toBe('2 / 3');
});

it('확대를 끝까지 눌러도 포커스가 뷰어 안에 남아 ← →가 동작한다', async () => {
  const zoomIn = button('확대');
  zoomIn.focus();
  for (let i = 0; i < 3; i++) await click(zoomIn);
  expect(zoomIn.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(zoomIn);

  await press(zoomIn, 'ArrowRight');
  expect(counter()).toBe('2 / 3');
});

const pointerDown = (el: HTMLElement) =>
  act(async () => {
    el.dispatchEvent(new Event('pointerdown', { bubbles: true }));
  });
const viewerBox = () => container.querySelector<HTMLElement>('[tabindex="-1"]')!;

it('마우스로 쥔 다음 버튼에서 ←를 누르면 포커스를 뷰어로 옮겨 우측 버튼에 링이 남지 않는다 (#924)', async () => {
  const next = button('다음 슬라이드');
  await pointerDown(next);
  next.focus();
  await click(next);
  expect(counter()).toBe('2 / 3');

  await press(next, 'ArrowLeft');
  expect(counter()).toBe('1 / 3');
  expect(document.activeElement).toBe(viewerBox());

  // 뷰어에 포커스가 남아 있으니 ← →는 계속 동작한다 (#818)
  await press(viewerBox(), 'ArrowRight');
  expect(counter()).toBe('2 / 3');
});

it('마우스 클릭 → ← → Tab → → 흐름: Tab 으로 닿은 버튼은 키보드 초점이라 포커스를 옮기지 않는다 (#924)', async () => {
  const next = button('다음 슬라이드');
  const prev = button('이전 슬라이드');
  await pointerDown(next);
  next.focus();
  await click(next);
  await press(next, 'ArrowLeft');
  expect(document.activeElement).toBe(viewerBox());

  // Tab 은 뷰어 상자에서 첫 버튼(이전)으로 간다. jsdom 은 Tab 이동을 하지 않아 직접 옮긴다
  await press(viewerBox(), 'Tab');
  prev.focus();
  await press(prev, 'ArrowRight');
  expect(counter()).toBe('2 / 3');
  expect(document.activeElement).toBe(prev);

  await press(prev, 'ArrowLeft');
  expect(counter()).toBe('1 / 3');
  expect(document.activeElement).toBe(prev);
});

it('마우스로 쥔 썸네일에서도 방향키를 누르면 포커스를 뷰어로 옮긴다', async () => {
  const thumb = button('3번 슬라이드');
  await pointerDown(thumb);
  thumb.focus();
  await click(thumb);
  expect(counter()).toBe('3 / 3');

  await press(thumb, 'ArrowLeft');
  expect(counter()).toBe('2 / 3');
  expect(document.activeElement).toBe(viewerBox());
});

it('뷰어 상자는 outline-none 을 두지 않아 방향키로 포커스가 옮겨 오면 전역 포커스 링이 그려진다', () => {
  expect(viewerBox().className).not.toContain('outline-none');
});
