// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useCreateStore } from '../createStore';
import { SlidePane } from './SlidePane';

vi.mock('@/shared/lib/usePdfDocument', () => ({
  usePdfDocument: () => null,
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  useCreateStore.getState().reset();
  useCreateStore.getState().attachSlides({
    pageCount: 2,
    fileUrl: '/slides.pdf',
    fileName: 'slides.pdf',
    presentationVersionId: 'pv1',
  });
  useCreateStore.getState().setSlideReview(null);

  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  useCreateStore.getState().reset();
  vi.unstubAllGlobals();
});

it('확인 필요와 확인 완료의 상태 배지, 아이콘, 버튼 접근성 이름을 구분한다', async () => {
  const slide = useCreateStore.getState().draft.slides[0]!;
  await act(async () => root.render(<SlidePane slide={slide} />));

  const status = () => host.querySelector<HTMLElement>('[role="status"]');
  const trigger = () => host.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]');

  expect(status()?.textContent?.trim()).toBe('확인 필요');
  expect(status()?.className).toContain('bg-coral-wash');
  expect(status()?.querySelector('circle')).not.toBeNull();
  expect(trigger()?.getAttribute('aria-label')).toBe('슬라이드 확인 · 확인 필요');

  await act(async () => useCreateStore.getState().confirmSlides(slide.version));

  expect(status()?.textContent?.trim()).toBe('확인 완료');
  expect(status()?.className).toContain('bg-panel');
  expect(status()?.querySelector('circle')).toBeNull();
  expect(trigger()?.getAttribute('aria-label')).toBe('슬라이드 확인 · 확인 완료');
});
