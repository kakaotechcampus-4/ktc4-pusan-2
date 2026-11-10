// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { forgetPrivacyConsentsInTab, privacyConsentKey } from '@/shared/lib/privacyConsent';
import { RequireSession } from './session';

const state = vi.hoisted(() => ({
  user: { id: 'u1', name: '테스트 사용자' } as { id: string; name: string } | null,
  logout: vi.fn(),
}));
vi.mock('./useSession', () => ({
  useSession: () => ({ isPending: false, isError: false, data: state.user }),
  useLogout: () => ({ isPending: false, isError: false, mutate: state.logout }),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  localStorage.clear();
  forgetPrivacyConsentsInTab();
  state.user = { id: 'u1', name: '테스트 사용자' };
  state.logout.mockReset();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router.dispose();
  host.remove();
  vi.unstubAllGlobals();
});

async function visit(path: string) {
  router = createMemoryRouter(
    [
      { path: '/welcome', element: <p>서비스 소개</p> },
      { path: '/login', element: <p>로그인</p> },
      {
        path: '/takes',
        element: <RequireSession />,
        children: [{ index: true, element: <p>Take 기록 화면</p> }],
      },
    ],
    { initialEntries: [path] },
  );
  await act(async () => root.render(<RouterProvider router={router} />));
}

function button(name: string) {
  const found = Array.from(host.querySelectorAll('button')).find((el) => el.textContent === name);
  expect(found).toBeDefined();
  return found!;
}

it('asks for consent before opening a signed-in screen', async () => {
  await visit('/takes');

  expect(host.querySelector('h1')?.textContent).toBe('개인정보 처리 및 데이터 활용');
  expect(host.textContent).toContain('Deepgram STT');
  expect(host.textContent).toContain('2026년 12월 31일까지 보관');
  expect(host.textContent).not.toContain('Take 기록 화면');
  expect(host.querySelector('a[href="/privacy"]')).not.toBeNull();
});

it('opens the requested screen at the same address once the user agrees', async () => {
  await visit('/takes');

  await act(async () => button('동의하고 시작하기').click());

  expect(host.textContent).toContain('Take 기록 화면');
  expect(router.state.location.pathname).toBe('/takes');
  expect(localStorage.getItem(privacyConsentKey('u1'))).not.toBeNull();
});

it('skips the notice for a user who already agreed in this browser', async () => {
  localStorage.setItem(privacyConsentKey('u1'), '2026-11-11T00:00:00.000Z');

  await visit('/takes');

  expect(host.textContent).toContain('Take 기록 화면');
  expect(host.textContent).not.toContain('개인정보 처리 및 데이터 활용');
});

it('asks another user on the same browser again', async () => {
  localStorage.setItem(privacyConsentKey('u1'), '2026-11-11T00:00:00.000Z');
  state.user = { id: 'u2', name: '다른 사용자' };

  await visit('/takes');

  expect(host.textContent).toContain('개인정보 처리 및 데이터 활용');
  expect(host.textContent).not.toContain('Take 기록 화면');
});

it('lets the user sign out instead of agreeing', async () => {
  await visit('/takes');

  await act(async () => button('동의하지 않고 로그아웃').click());

  expect(state.logout).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem(privacyConsentKey('u1'))).toBeNull();
});

it('sends signed-out visitors to sign in before any consent notice', async () => {
  state.user = null;

  await visit('/takes');

  expect(router.state.location.pathname).toBe('/login');
  expect(host.textContent).not.toContain('개인정보 처리 및 데이터 활용');
});
