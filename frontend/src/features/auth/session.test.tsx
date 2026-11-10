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
  logoutPending: false,
}));
vi.mock('./useSession', () => ({
  useSession: () => ({ isPending: false, isError: false, data: state.user }),
  useLogout: () => ({ isPending: state.logoutPending, isError: false, mutate: state.logout }),
}));
vi.mock('@/shared/api/useRawDataConsentLock', () => ({ useRawDataConsentLock: () => undefined }));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  // jsdom 은 모달을 열고 닫지 않습니다 — open 만 맞춥니다
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  localStorage.clear();
  forgetPrivacyConsentsInTab();
  state.user = { id: 'u1', name: '테스트 사용자' };
  state.logout.mockReset();
  state.logoutPending = false;
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

async function visit(path: string, { holdUntilConsent = false } = {}) {
  router = createMemoryRouter(
    [
      { path: '/welcome', element: <p>서비스 소개</p> },
      { path: '/login', element: <p>로그인</p> },
      {
        path: '/takes',
        element: <RequireSession holdUntilConsent={holdUntilConsent} />,
        children: [{ index: true, element: <h1>Take 기록 화면</h1> }],
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

const consentDialog = () => host.querySelector<HTMLDialogElement>('dialog');

it('opens the consent popup over the signed-in screen before the user agrees', async () => {
  await visit('/takes');

  const dialog = consentDialog();
  expect(dialog?.open).toBe(true);
  expect(dialog?.querySelector('h2')?.textContent).toBe('개인정보 처리 및 데이터 활용');
  expect(dialog?.textContent).toContain('Deepgram STT');
  // 영상은 보내지 않고 시선 구간만 저장한다는 것 · 보관 기한이 처리방침과 같게 보입니다
  expect(dialog?.textContent).toContain('시선 분석 결과(시선 구간)만 연습 기록으로 저장합니다');
  expect(dialog?.textContent).toContain('2026년 12월 31일까지 보관합니다');
  expect(dialog?.querySelector('a[href="/privacy"]')).not.toBeNull();
  // 뒤에는 가려던 화면이 그려져 있고, 모달이라 누를 수 없습니다
  expect(host.querySelector('h1')?.textContent).toBe('Take 기록 화면');
  // 안내문부터 읽히도록 첫 초점은 창 제목입니다
  expect(document.activeElement).toBe(dialog?.querySelector('h2'));
});

it('does not close the popup with Esc before the user agrees', async () => {
  await visit('/takes');

  const cancel = new Event('cancel', { cancelable: true });
  await act(async () => consentDialog()!.dispatchEvent(cancel));

  expect(cancel.defaultPrevented).toBe(true);
  expect(consentDialog()?.open).toBe(true);
});

it('reopens the popup if the browser force-closes it', async () => {
  await visit('/takes');

  await act(async () => {
    consentDialog()!.close();
    consentDialog()!.dispatchEvent(new Event('close'));
  });

  expect(consentDialog()?.open).toBe(true);
});

it('keeps camera screens unrendered behind the popup until the user agrees', async () => {
  await visit('/takes', { holdUntilConsent: true });

  expect(consentDialog()?.open).toBe(true);
  expect(host.textContent).not.toContain('Take 기록 화면');

  await act(async () => button('동의하고 시작하기').click());

  expect(host.querySelector('h1')?.textContent).toBe('Take 기록 화면');
});

it('closes the popup at the same address and focuses the screen heading once the user agrees', async () => {
  await visit('/takes');

  await act(async () => button('동의하고 시작하기').click());

  expect(consentDialog()).toBeNull();
  expect(router.state.location.pathname).toBe('/takes');
  expect(localStorage.getItem(privacyConsentKey('u1'))).not.toBeNull();
  // body 의 글자도 'Take 기록 화면' 뿐이라, 글자가 아니라 요소로 봅니다
  await vi.waitFor(() => expect(document.activeElement).toBe(host.querySelector('h1')));
});

it('closes the popup when the user agrees in another tab', async () => {
  await visit('/takes');
  expect(consentDialog()).not.toBeNull();

  // 다른 탭이 적은 값은 이 탭에 storage 이벤트로만 알려집니다
  await act(async () => {
    localStorage.setItem(privacyConsentKey('u1'), '2026-11-11T00:00:00.000Z');
    window.dispatchEvent(new StorageEvent('storage', { key: privacyConsentKey('u1') }));
  });

  expect(consentDialog()).toBeNull();
});

it('skips the popup for a user who already agreed in this browser', async () => {
  localStorage.setItem(privacyConsentKey('u1'), '2026-11-11T00:00:00.000Z');

  await visit('/takes');

  expect(host.textContent).toContain('Take 기록 화면');
  expect(consentDialog()).toBeNull();
});

it('asks another user on the same browser again', async () => {
  localStorage.setItem(privacyConsentKey('u1'), '2026-11-11T00:00:00.000Z');
  state.user = { id: 'u2', name: '다른 사용자' };

  await visit('/takes');

  expect(consentDialog()?.open).toBe(true);
});

it('lets the user sign out instead of agreeing', async () => {
  await visit('/takes');

  await act(async () => button('동의하지 않고 로그아웃').click());

  expect(state.logout).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem(privacyConsentKey('u1'))).toBeNull();
});

it('keeps focus inside the popup while signing out', async () => {
  state.logoutPending = true;
  await visit('/takes');

  const signOut = button('로그아웃 중…');
  // disabled 면 초점이 창 밖으로 빠집니다 (#845)
  expect(signOut.disabled).toBe(false);
  expect(signOut.getAttribute('aria-disabled')).toBe('true');
  await act(async () => signOut.click());
  expect(state.logout).not.toHaveBeenCalled();
});

it('sends signed-out visitors to sign in before any consent notice', async () => {
  state.user = null;

  await visit('/takes');

  expect(router.state.location.pathname).toBe('/login');
  expect(consentDialog()).toBeNull();
});
