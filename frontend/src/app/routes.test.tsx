// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider, useParams } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { appRoutes, router as browserRouter } from './routes';

const state = vi.hoisted(() => ({ signedIn: false, stageImports: 0 }));
vi.mock('@/features/auth/useSession', () => ({
  useSession: () => ({
    isPending: false,
    isError: false,
    data: state.signedIn ? { name: '테스트 사용자' } : null,
  }),
  useLogout: () => ({ isPending: false, isError: false }),
}));
vi.mock('@/features/onboarding/WelcomePage', () => ({ WelcomePage: () => <p>서비스 소개</p> }));
vi.mock('@/features/auth/LoginPage', () => ({ LoginPage: () => <p>로그인</p> }));
vi.mock('@/features/pitch/AppHeader', () => ({ AppHeader: () => <header>메뉴</header> }));
vi.mock('@/features/pitch/HomePage', () => ({ HomePage: () => <p>내 피치</p> }));
vi.mock('@/features/rehearsal/stage/RehearsalPage', () => {
  state.stageImports++;
  return { RehearsalPage: () => <p>리허설 {useParams().takeId}</p> };
});
vi.mock('@/features/info/AboutPage', () => {
  throw new Error('Failed to fetch dynamically imported module');
});

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter> | undefined;

beforeEach(() => {
  browserRouter.dispose();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router?.dispose();
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function visit(path: string) {
  await act(async () => root.render(null));
  router?.dispose();
  router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  await act(async () => root.render(<RouterProvider router={router!} />));
}

it('loads rehearsal only after the session gate, preserving take routes and titles', async () => {
  state.signedIn = false;
  await visit('/takes/review-take/rehearsal');
  expect(router!.state.location.pathname).toBe('/login');
  expect(state.stageImports).toBe(0);

  state.signedIn = true;
  await visit('/takes/review-take/rehearsal');
  await vi.waitFor(() => expect(host.textContent).toContain('리허설 review-take'));
  expect(state.stageImports).toBe(1);
  expect(document.title).toBe('실시간 코칭 리허설 | 피치코치');
  await act(async () => {
    await router!.navigate('/takes/next-take/exam');
  });
  expect(host.textContent).toContain('리허설 next-take');
  expect(state.stageImports).toBe(1);
  expect(document.title).toBe('실전 검증 리허설 | 피치코치');
});

it('offers recovery when a screen chunk fails and can navigate back to the eager home', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const onExpectedError = (event: ErrorEvent) => event.preventDefault();
  window.addEventListener('error', onExpectedError);
  state.signedIn = true;
  await visit('/about');
  await vi.waitFor(() =>
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(
      '화면을 불러오지 못했어요.',
    ),
  );
  expect(host.querySelector('button')?.textContent).toBe('새로고침');
  expect(host.querySelector('a')?.getAttribute('href')).toBe('/');
  await act(async () => host.querySelector<HTMLAnchorElement>('a')!.click());
  expect(host.textContent).toContain('내 피치');
  expect(host.querySelector('[role="alert"]')).toBeNull();
  window.removeEventListener('error', onExpectedError);
});
