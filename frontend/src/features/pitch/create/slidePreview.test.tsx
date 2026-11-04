// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SlidePreviewDialog } from './steps/SlidePreviewDialog';

vi.mock('@/shared/ui/PdfPage', () => ({ PdfPage: () => null }));

// jsdom 에는 showModal · close 가 없습니다 — 열림 표시와 close 이벤트만 흉내 냅니다
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
});

const doc = { numPages: 3 } as PDFDocumentProxy;
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  document.body.style.overflow = '';
  vi.unstubAllGlobals();
});

async function renderDialog(page: number, onPage = vi.fn(), onClose = vi.fn()) {
  await act(async () =>
    root.render(<SlidePreviewDialog doc={doc} page={page} onPage={onPage} onClose={onClose} />),
  );
  return { dialog: container.querySelector('dialog')!, onPage, onClose };
}

async function press(dialog: HTMLDialogElement, key: string) {
  await act(async () => {
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });
}

it('첫 장에서 ←를 눌러도 1장 밖으로 나가지 않는다', async () => {
  const { dialog, onPage } = await renderDialog(1);
  await press(dialog, 'ArrowLeft');
  expect(onPage).toHaveBeenCalledWith(1);
  expect(container.querySelector('[aria-label="이전 슬라이드"]')).toHaveProperty('disabled', true);
});

it('마지막 장에서 →를 눌러도 마지막 장 밖으로 나가지 않는다', async () => {
  const { dialog, onPage } = await renderDialog(3);
  await press(dialog, 'ArrowRight');
  expect(onPage).toHaveBeenCalledWith(3);
  expect(container.querySelector('[aria-label="다음 슬라이드"]')).toHaveProperty('disabled', true);
});

it('열려 있는 동안 배경 스크롤을 잠그고, 닫은 뒤 원래 값으로 돌려놓는다', async () => {
  document.body.style.overflow = 'scroll';
  const { onClose } = await renderDialog(
    2,
    vi.fn(),
    vi.fn(() => root.render(null)),
  );
  expect(document.body.style.overflow).toBe('hidden');

  const closeButton = [...container.querySelectorAll('button')].find(
    (b) => b.textContent === '닫기',
  )!;
  await act(async () => closeButton.click());

  expect(onClose).toHaveBeenCalledOnce();
  expect(container.querySelector('dialog')).toBeNull();
  expect(document.body.style.overflow).toBe('scroll');
});

it('닫기를 거치지 않고 언마운트돼도 원래 스크롤 값으로 돌려놓는다', async () => {
  document.body.style.overflow = 'auto';
  await renderDialog(1);
  expect(document.body.style.overflow).toBe('hidden');
  await act(async () => root.unmount());
  root = createRoot(container);
  expect(document.body.style.overflow).toBe('auto');
});
