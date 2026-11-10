// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppHeader } from '@/shared/ui/AppHeader';
import { DemoPitchError, loadPitch } from './loadPitch';
import { PitchEditPage } from './PitchCreatePage';

vi.mock('@/shared/api/useLogout', () => ({
  useLogout: () => ({ isPending: false, isError: false }),
}));
// 샘플 피치 오류(DemoPitchError)는 화면이 instanceof 로 가리므로 진짜를 둡니다
vi.mock('./loadPitch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./loadPitch')>()),
  loadPitch: vi.fn(),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () =>
    root.render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <MemoryRouter initialEntries={[{ pathname: '/pitch/p1/edit', key: 'edit-notice' }]}>
          <AppHeader />
          <Routes>
            <Route path="/pitch/:id/edit" element={<PitchEditPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

async function skip() {
  const main = host.querySelector('main')!;
  main.scrollIntoView = vi.fn();
  await act(async () => {
    host.querySelector<HTMLAnchorElement>('a[href="#main"]')!.click();
  });
  return main;
}

it('불러오는 중 안내에서도 건너뛰기 링크가 본문으로 초점을 옮긴다', async () => {
  vi.mocked(loadPitch).mockReturnValue(new Promise(() => undefined));
  await mount();
  expect(host.querySelectorAll('main#main')).toHaveLength(1);
  expect(host.querySelector('main')!.textContent).toContain('불러오는 중');
  expect(await skip()).toBe(document.activeElement);
  expect(window.location.hash).toBe('');
});

it('불러오지 못한 안내에서도 본문이 하나이고 링크가 초점을 옮긴다', async () => {
  vi.mocked(loadPitch).mockRejectedValue(new Error('network'));
  await mount();
  await vi.waitFor(() =>
    expect(host.querySelector('main')!.textContent).toContain('불러오지 못했어요'),
  );
  expect(host.querySelectorAll('main#main')).toHaveLength(1);
  expect(await skip()).toBe(document.activeElement);
  expect(window.location.hash).toBe('');
});

it('튜토리얼 샘플 피치는 수정 화면을 열지 않고 새 피치 만들기로 안내한다', async () => {
  vi.mocked(loadPitch).mockRejectedValue(new DemoPitchError());
  await mount();
  await vi.waitFor(() =>
    expect(host.querySelector('main')!.textContent).toContain('샘플 피치는 고칠 수 없어요'),
  );
  const hrefs = [...host.querySelectorAll('main a')].map((a) => a.getAttribute('href'));
  expect(hrefs).toEqual(['/pitch/new', '/']);
  // 샘플을 막은 것은 실패가 아니라 콘솔에 남기지 않습니다
  expect(console.error).not.toHaveBeenCalledWith(
    '[피치 수정] 피치를 불러오지 못했습니다',
    expect.anything(),
  );
});
