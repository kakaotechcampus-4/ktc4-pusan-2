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
