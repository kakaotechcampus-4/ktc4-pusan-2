// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider, useParams } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { appRoutes, router as browserRouter } from './routes';

const state = vi.hoisted(() => ({ signedIn: false, stageImports: 0, launchOpen: true }));
vi.mock('@/features/auth/useSession', () => ({
  useSession: () => ({
    isPending: false,
    isError: false,
    data: state.signedIn ? { name: '테스트 사용자' } : null,
  }),
  useLogout: () => ({ isPending: false, isError: false }),
}));
vi.mock('@/shared/api/launch', () => ({
  useLaunchStatus: () => ({ isOpen: state.launchOpen }),
}));
vi.mock('@/features/onboarding/WelcomePage', () => ({ WelcomePage: () => <p>서비스 소개</p> }));
vi.mock('@/features/auth/LoginPage', () => ({ LoginPage: () => <p>로그인</p> }));
vi.mock('@/shared/ui/AppHeader', () => ({ AppHeader: () => <header>메뉴</header> }));
vi.mock('@/features/pitch/HomePage', () => ({ HomePage: () => <p>내 피치</p> }));
vi.mock('@/features/rehearsal/stage/RehearsalPage', () => {
  state.stageImports++;
  return { RehearsalPage: () => <p>리허설 {useParams().takeId}</p> };
});
vi.mock('@/features/rehearsal/prepare/DeviceCheckPage', () => ({
  DeviceCheckPage: () => <p>시작 전 세팅 {useParams().pitchId}</p>,
}));
vi.mock('@/features/report/ConfirmPage', () => ({
  ConfirmPage: () => <p>대본 확인 {useParams().takeId}</p>,
}));
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
  state.launchOpen = false;
  state.signedIn = true;
  await visit('/takes/review-take/rehearsal');
  expect(router!.state.location.pathname).toBe('/welcome');
  expect(state.stageImports).toBe(0);
  state.launchOpen = true;
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

it('preserves confirmation route, title and header ownership after lazy loading', async () => {
  state.signedIn = true;
  await visit('/takes/confirm-take/confirm');
  await vi.waitFor(() => expect(host.textContent).toContain('대본 확인 confirm-take'));
  expect(document.title).toBe('대본 확인 | 피치코치');
  expect(host.querySelector('header')).toBeNull();
});

it('puts the app header on the setup screen and keeps the name row only on the stage', async () => {
  state.signedIn = true;
  await visit('/pitch/p1/device-check');
  await vi.waitFor(() => expect(host.textContent).toContain('시작 전 세팅 p1'));
  expect(host.querySelector('header')?.textContent).toBe('메뉴');
  expect(host.textContent).not.toContain('테스트 사용자');

  await visit('/takes/stage-take/rehearsal');
  await vi.waitFor(() => expect(host.textContent).toContain('리허설 stage-take'));
  expect(host.querySelector('header')).toBeNull();
  expect(host.textContent).toContain('테스트 사용자');
});

it('shows welcome to signed-out root visitors after launch and home to signed-in visitors', async () => {
  state.launchOpen = true;
  state.signedIn = false;
  await visit('/?utm_source=share');
  expect(router!.state.location.pathname).toBe('/welcome');
  expect(host.textContent).toBe('서비스 소개');

  await visit('/takes?sort=recent#latest');
  expect(router!.state.location.pathname).toBe('/login');
  expect(new URLSearchParams(router!.state.location.search).get('next')).toBe(
    '/takes?sort=recent#latest',
  );

  await visit('/?auth_error=access_denied');
  expect(router!.state.location.pathname).toBe('/login');
  expect(new URLSearchParams(router!.state.location.search).get('auth_error')).toBe(
    'access_denied',
  );

  state.signedIn = true;
  await visit('/');
  expect(router!.state.location.pathname).toBe('/');
  expect(host.textContent).toContain('내 피치');
});

it('sends touch-only devices from practice routes to the unsupported screen, even before login', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(pointer: coarse)' }));
  state.launchOpen = true;
  state.signedIn = false;
  for (const path of ['/pitch/tutorial', '/pitch/p1/device-check', '/takes/t1/rehearsal', '/takes/t1/exam']) {
    await visit(path);
    expect(router!.state.location.pathname).toBe('/unsupported');
  }
});

it('keeps home and report-side routes open on touch-only devices', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(pointer: coarse)' }));
  state.launchOpen = true;
  state.signedIn = true;
  await visit('/');
  expect(router!.state.location.pathname).toBe('/');
  await visit('/takes/t1/confirm');
  expect(router!.state.location.pathname).toBe('/takes/t1/confirm');
});

it('lets devices with a mouse or trackpad into practice routes', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(pointer: fine)' || query === '(any-pointer: fine)',
  }));
  state.launchOpen = true;
  state.signedIn = true;
  await visit('/pitch/tutorial');
  expect(router!.state.location.pathname).toBe('/pitch/tutorial');
  await visit('/takes/t1/rehearsal');
  expect(router!.state.location.pathname).toBe('/takes/t1/rehearsal');
});
