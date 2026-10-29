/** 옮긴 시안이 모두 설계서 안에서 오류 없이 그려지는지 확인한다. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { DesignDraftRoute, DesignIndex } from './DesignIndex';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = '';
});

function render(path: string) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  act(() =>
    root!.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<DesignIndex />} />
          <Route path="/:name" element={<DesignDraftRoute />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
  return container;
}

it('목록의 시안 주소가 모두 시안을 연다', () => {
  const index = render('/');
  const names = [...index.querySelectorAll<HTMLAnchorElement>('a[href^="/"]')]
    .map((a) => a.getAttribute('href')!.slice(1))
    .filter(Boolean);
  expect(names.length).toBeGreaterThan(10);
  act(() => root?.unmount());

  for (const name of names) {
    const page = render(`/${name}`);
    expect(page.textContent, name).not.toBe('');
    act(() => root?.unmount());
  }
});

it('구현된 화면은 제품 개발 서버의 새 탭으로 연다', () => {
  const index = render('/');
  const live = index.querySelector<HTMLAnchorElement>('a[href^="http://localhost:3000"]');
  expect(live?.target).toBe('_blank');
});
