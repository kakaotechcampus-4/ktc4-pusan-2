// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useCreateStore } from './createStore';
import { SlidePane } from './steps/SlidePane';

vi.mock('@/shared/lib/pdf', () => ({ openPdf: vi.fn() }));

const state = () => useCreateStore.getState();
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  state().reset();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

async function renderSlide() {
  await act(async () => root.render(<SlidePane slide={null} />));
  return container.textContent;
}

it('첫 업로드 전에 자동 생성과 실패·이탈 시 보관 사실을 알린다', async () => {
  const markup = await renderSlide();
  expect(markup).toContain('PDF를 올리면 피치가 자동으로 만들어져요.');
  expect(markup).toContain('업로드에 실패하거나 나가도 내 피치에 남아');
});

it('기존 피치에 자료를 추가할 때는 새 피치 생성 안내를 하지 않는다', async () => {
  state().setPitchId('existing');
  expect(await renderSlide()).not.toContain('자동으로 만들어져요');
});

it('업로드 실패 뒤 피치가 남아 있으면 같은 피치에서 재시도하도록 안내한다', async () => {
  state().setPitchId('existing');
  state().setSlideUpload({ status: 'failed', file: new File([], 'a.pdf'), reason: 'upload' });
  const markup = await renderSlide();
  expect(markup).toContain('피치는 내 피치에 보관돼 있어요.');
  expect(markup).toContain('다시 시도');
});
