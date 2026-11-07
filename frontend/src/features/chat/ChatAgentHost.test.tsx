// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChatAgentHost } from './ChatAgentHost';

const state = vi.hoisted(() => ({
  calls: 0,
  session: { isPending: false, isError: false, data: { id: 'u1' } as { id: string } | null },
}));
vi.mock('@/shared/api/useSession', () => ({
  useSession: () => {
    state.calls++;
    return state.session;
  },
}));

let host: HTMLDivElement;
let root: Root;
let router: ReturnType<typeof createMemoryRouter>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubEnv('VITE_CHAT_AGENT', 'true');
  state.calls = 0;
  state.session = { isPending: false, isError: false, data: { id: 'u1' } };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router.dispose();
  host.remove();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

// routes.tsx 의 최상위 레이아웃과 같은 모양 — 화면이 바뀌어도 채팅은 같은 자리에 남습니다
async function visit(path: string) {
  router = createMemoryRouter(
    [
      {
        element: (
          <>
            <Outlet />
            <ChatAgentHost />
          </>
        ),
        children: [
          { path: '/', element: <button type="button">홈 버튼</button> },
          { path: '/takes/:takeId', element: <p>리포트</p> },
          { path: '/takes/:takeId/rehearsal', element: <p>리허설</p> },
          { path: '/pitch/new', element: <p>피치 만들기</p> },
          { path: '/pitch/:pitchId/device-check', element: <p>장치 점검</p> },
          { path: '/welcome', element: <p>서비스 소개</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  await act(async () => root.render(<RouterProvider router={router} />));
}

const go = (path: string) => act(async () => router.navigate(path));
const launcher = () => host.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]');
const panel = () => host.querySelector<HTMLElement>('[role="dialog"]');
const input = () => host.querySelector('textarea')!;
const sendButton = () =>
  [...host.querySelectorAll('button')].find((button) => button.textContent === '입력')!;
const sentMessages = () =>
  [...panel()!.querySelectorAll('p.self-end')].map((message) => message.textContent);

async function openPanel() {
  launcher()!.focus();
  await act(async () => launcher()!.click());
}

function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
  return act(async () => {
    setter.call(input(), value);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function press(target: Element, init: KeyboardEventInit) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  await act(async () => {
    target.dispatchEvent(event);
  });
  return event;
}

it('런처로 열면 입력칸에 초점이 가고, 닫으면 런처로 돌아온다', async () => {
  await visit('/');
  await openPanel();
  expect(panel()).not.toBeNull();
  expect(launcher()!.getAttribute('aria-expanded')).toBe('true');
  expect(document.activeElement).toBe(input());

  const close = [...host.querySelectorAll('button')].find((b) => b.textContent === '닫기')!;
  await act(async () => close.click());
  expect(panel()).toBeNull();
  expect(document.activeElement).toBe(launcher());
});

it('Esc 로 닫고 런처로 초점을 돌린다', async () => {
  await visit('/');
  await openPanel();
  await press(input(), { key: 'Escape' });
  expect(panel()).toBeNull();
  expect(document.activeElement).toBe(launcher());
});

it('Enter 는 보내고 Shift+Enter 는 줄을 바꾼다', async () => {
  await visit('/');
  await openPanel();
  await type('첫 줄');
  const shiftEnter = await press(input(), { key: 'Enter', shiftKey: true });
  expect(shiftEnter.defaultPrevented).toBe(false);
  expect(sentMessages()).toEqual([]);

  const enter = await press(input(), { key: 'Enter' });
  expect(enter.defaultPrevented).toBe(true);
  expect(sentMessages()).toEqual(['첫 줄']);
  expect(input().value).toBe('');
});

it('한글 조합 중의 Enter 는 보내지 않는다', async () => {
  await visit('/');
  await openPanel();
  await type('안녕');
  const composing = await press(input(), { key: 'Enter', isComposing: true });
  expect(composing.defaultPrevented).toBe(false);
  expect(sentMessages()).toEqual([]);
  expect(input().value).toBe('안녕');
});

it('버튼으로 보낸 뒤에도 초점이 패널 안에 남아 Esc 로 닫힌다', async () => {
  await visit('/');
  await openPanel();
  await type('질문');
  const send = sendButton();
  send.focus();
  await act(async () => send.click());
  expect(sentMessages()).toEqual(['질문']);
  // `disabled` 가 되면 Chrome 은 누른 버튼의 초점을 body 로 옮깁니다 (#818)
  expect(send.disabled).toBe(false);
  expect(send.getAttribute('aria-disabled')).toBe('true');
  expect(panel()!.contains(document.activeElement)).toBe(true);

  // 빈 입력으로 다시 눌러도 보내지 않습니다
  await act(async () => send.click());
  expect(sentMessages()).toEqual(['질문']);

  await press(document.activeElement!, { key: 'Escape' });
  expect(panel()).toBeNull();
});

it('채팅이 숨는 화면에 갔다 돌아와도 패널을 다시 열어 초점을 빼앗지 않는다', async () => {
  await visit('/');
  await openPanel();
  await type('남길 말');
  await go('/takes/t1/rehearsal');
  expect(launcher()).toBeNull();

  await go('/');
  expect(panel()).toBeNull();
  expect(host.querySelector('textarea')).toBeNull();
  expect(launcher()!.getAttribute('aria-expanded')).toBe('false');

  // 입력과 보낸 메시지는 화면을 옮겨도 남습니다
  await openPanel();
  expect(input().value).toBe('남길 말');
});

it('세션 재조회가 실패해도 로그인 정보가 있으면 열린 패널을 유지한다', async () => {
  await visit('/');
  await openPanel();
  state.session = { isPending: false, isError: true, data: { id: 'u1' } };
  await go('/takes/t1');
  expect(panel()).not.toBeNull();
});

it('로그인 정보가 없으면 그리지 않는다', async () => {
  state.session = { isPending: false, isError: false, data: null };
  await visit('/');
  expect(launcher()).toBeNull();
});

it('채팅을 띄우지 않는 화면에서는 세션을 구독하지 않는다', async () => {
  await visit('/welcome');
  await go('/takes/t1/rehearsal');
  expect(state.calls).toBe(0);
  expect(launcher()).toBeNull();

  await go('/');
  expect(state.calls).toBeGreaterThan(0);
  expect(launcher()).not.toBeNull();
});

it('빌드 스위치가 꺼져 있으면 그리지 않고 세션도 구독하지 않는다', async () => {
  vi.stubEnv('VITE_CHAT_AGENT', 'false');
  await visit('/');
  expect(launcher()).toBeNull();
  expect(state.calls).toBe(0);
});

it('열린 패널은 피치 만들기 · 장치 점검으로 옮겨도 남고, 리허설에 들어가면 숨는다', async () => {
  await visit('/');
  await openPanel();
  await type('연습 준비 중');
  await go('/pitch/new');
  expect(panel()).not.toBeNull();
  await go('/pitch/p1/device-check');
  expect(panel()).not.toBeNull();
  expect(input().value).toBe('연습 준비 중');

  await go('/takes/t1/rehearsal');
  expect(launcher()).toBeNull();
  expect(panel()).toBeNull();
});

it('2,000자는 코드 포인트로 세고, 넘으면 입력을 둔 채 보내지 않는다', async () => {
  await visit('/');
  await openPanel();
  // 이모지는 UTF-16 으로 2자지만 서버(Python len)처럼 1자로 셉니다
  await type('😀'.repeat(2000));
  expect(host.querySelector('[role="alert"]')).toBeNull();
  await press(input(), { key: 'Enter' });
  expect(sentMessages()).toHaveLength(1);

  const tooLong = 'a'.repeat(2001);
  await type(tooLong);
  expect(host.querySelector('[role="alert"]')!.textContent).toContain('2,000자까지');
  expect(input().getAttribute('aria-invalid')).toBe('true');
  expect(sendButton().getAttribute('aria-disabled')).toBe('true');
  await press(input(), { key: 'Enter' });
  await act(async () => sendButton().click());
  expect(sentMessages()).toHaveLength(1);
  expect(input().value).toBe(tooLong);
});

it('닫혀 있을 때는 런처가 없는 패널을 가리키지 않는다', async () => {
  await visit('/');
  expect(launcher()!.hasAttribute('aria-controls')).toBe(false);
  await openPanel();
  expect(launcher()!.getAttribute('aria-controls')).toBe(panel()!.id);
});
