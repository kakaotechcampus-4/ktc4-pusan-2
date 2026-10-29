/** 옛 시안이 모두 설계서 뷰어 안에서 오류 없이 그려지고, 옛 주소(`#/<이름>`)로도 열리는지 확인한다. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, it, vi } from 'vitest';
import { screens } from '@/catalog';
import { DesignRoutes } from '@/DesignGuide';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

function render(path: string) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  act(() =>
    root!.render(
      <MemoryRouter initialEntries={[path]}>
        <DesignRoutes />
      </MemoryRouter>,
    ),
  );
  return container;
}

const drafts = screens.filter((screen) => screen.status === '시안만');

it('옛 주소마다 그 시안을 뷰어의 미리보기로 열고, 미리보기 주소에서 오류 없이 그린다', () => {
  const errors = vi.spyOn(console, 'error');
  expect(drafts.length).toBeGreaterThan(10);
  for (const draft of drafts) {
    const page = render(draft.route);
    const frame = page.querySelector<HTMLIFrameElement>(
      `[role="region"][aria-label="${draft.title} 미리보기"] iframe`,
    );
    expect(frame?.getAttribute('src'), draft.route).toContain(`#/frame?screen=${draft.id}&`);
    act(() => root?.unmount());
    // iframe이 여는 주소. jsdom은 iframe 안을 불러오지 않아 그 주소를 직접 그린다
    const preview = render(`/frame?screen=${draft.id}&state=default`);
    expect(preview.textContent, draft.route).toBeTruthy();
    act(() => root?.unmount());
  }
  expect(errors).not.toHaveBeenCalled();
});

it('없는 시안 이름이면 기본 화면을 연다', () => {
  const page = render('/no-such-draft');
  expect(page.querySelector('[aria-current="page"]')?.textContent).toBe(screens[0]!.title);
});

it('제품에서 열기는 제품 개발 서버를 새 탭으로 연다', () => {
  const page = render('/report');
  const live = page.querySelector<HTMLAnchorElement>('a[href="http://localhost:3000/takes/t3"]');
  expect(live).toBeTruthy();
  expect(live?.target).toBe('_blank');
});
