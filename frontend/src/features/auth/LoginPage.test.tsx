// @vitest-environment jsdom
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LoginPage } from './LoginPage';

const env = vi.hoisted(() => ({ desktopChrome: true, touchOnly: false }));
vi.mock('@/shared/lib/browserCheck', () => ({
  isDesktopChrome: () => env.desktopChrome,
  isTouchOnlyDevice: () => env.touchOnly,
}));
const session = vi.hoisted(() => ({
  isPending: false,
  isError: false,
  data: null as { id: string } | null,
  refetch: () => Promise.resolve(),
}));
vi.mock('./useSession', () => ({
  useSession: () => session,
  safeDestination: (next: string | null) => next ?? '/',
}));

const { showModal, close } = HTMLDialogElement.prototype;
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  // 브라우저처럼 close 이벤트를 다음 작업에서 냅니다
  HTMLDialogElement.prototype.close = function () {
    if (!this.open) return;
    this.open = false;
    setTimeout(() => this.dispatchEvent(new Event('close')));
  };
  Object.assign(env, { desktopChrome: true, touchOnly: false });
  Object.assign(session, { isPending: false, isError: false, data: null });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  HTMLDialogElement.prototype.showModal = showModal;
  HTMLDialogElement.prototype.close = close;
  vi.unstubAllGlobals();
});

async function render({ strict = false } = {}) {
  const page = (
    <MemoryRouter initialEntries={['/login']}>
      <LoginPage />
    </MemoryRouter>
  );
  await act(async () => root.render(strict ? <StrictMode>{page}</StrictMode> : page));
}
const flush = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));
const dialog = () => host.querySelector('dialog');
const confirmButton = () =>
  [...host.querySelectorAll('button')].find((button) => button.textContent === '확인')!;
const loginButton = () => host.querySelector('button[aria-label="Google 계정으로 로그인"]');

it('PC 의 Chrome 이면 안내를 띄우지 않는다', async () => {
  await render();
  expect(dialog()).toBeNull();
});

it('PC 의 다른 브라우저면 안내를 띄우고, 닫으면 Google 로그인 버튼으로 초점을 옮긴다', async () => {
  env.desktopChrome = false;
  await render();
  expect(dialog()?.open).toBe(true);
  expect(dialog()?.textContent).toContain('PC의 Chrome에서 열어 주세요');
  expect(dialog()?.textContent).toContain('시선을 재지 못할 수 있어요');
  expect(document.activeElement).toBe(confirmButton());

  await act(async () => confirmButton().click());
  await flush();
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(loginButton());
});

it('Esc 처럼 브라우저가 닫아도 안내를 거두고 로그인 버튼으로 간다', async () => {
  env.desktopChrome = false;
  await render();
  await act(async () => dialog()!.close());
  await flush();
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(loginButton());
});

it('휴대폰 · 태블릿이면 연습 화면이 열리지 않는다고 알린다', async () => {
  Object.assign(env, { desktopChrome: false, touchOnly: true });
  await render();
  expect(dialog()?.textContent).toContain('장치 점검과 연습 화면이 열리지 않아요');
  expect(dialog()?.textContent).not.toContain('시선을 재지 못할 수 있어요');
});

it('로그인 상태를 확인하는 동안은 띄우지 않고, 로그인 화면이 남으면 띄운다', async () => {
  env.desktopChrome = false;
  session.isPending = true;
  await render();
  expect(dialog()).toBeNull();

  session.isPending = false;
  await render();
  expect(dialog()?.open).toBe(true);
});

it('이미 로그인했으면 띄우지 않고 넘어간다', async () => {
  env.desktopChrome = false;
  session.data = { id: 'u1' };
  await render();
  expect(dialog()).toBeNull();
});

it('닫은 기록을 남기지 않아 로그인 화면을 다시 열면 또 띄운다', async () => {
  env.desktopChrome = false;
  await render();
  await act(async () => confirmButton().click());
  await flush();
  expect(dialog()).toBeNull();

  await act(async () => root.unmount());
  root = createRoot(host);
  await render();
  expect(dialog()?.open).toBe(true);
});

it('StrictMode 가 두 번 마운트해도 창이 열린 채로 남는다', async () => {
  env.desktopChrome = false;
  await render({ strict: true });
  await flush();
  expect(dialog()?.open).toBe(true);
});
