// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { registerChatScreen, resetChatScreen, type ScreenBody } from '@/shared/lib/chatScreen';
import type { SendMessageRequest } from '@/types/chat';
import { ChatAgentHost } from './ChatAgentHost';
import { AVAILABILITY_RETRY_MS, AVAILABILITY_TIMEOUT_MS } from './chatAvailability';
import { emptyBasis, readCard, type ShownClaim } from './chatResponse';
import { chatTicket, useChatStore } from './chatStore';
import type { TransportSignal } from './chatTransport';

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

/** 서버 — 경로마다 답을 바꿔 끼웁니다 */
const server = vi.hoisted(() => ({
  seen: [] as { path: string; signal: AbortSignal | null }[],
  availability: (_signal: AbortSignal | null): Promise<unknown> =>
    Promise.resolve({ enabled: true }),
  messages: (): Promise<unknown> => Promise.resolve({ messages: [], next_before_seq: null }),
  report: (takeId: string): Promise<unknown> =>
    Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' }),
}));
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  apiRequest: (path: string, init: RequestInit = {}) => {
    server.seen.push({ path, signal: init.signal ?? null });
    if (path === '/api/chat/availability') return server.availability(init.signal ?? null);
    if (path.startsWith('/api/chat/conversations/')) return server.messages();
    const take = /^\/api\/takes\/([^/]+)\/report$/.exec(path);
    if (take) return server.report(take[1]!);
    return Promise.reject(new Error(`모르는 경로 ${path}`));
  },
}));

/** 통신 계층 대역 — 보내기는 저장소의 잠금까지만 하고(보내는 중), 신호는 시험이 냅니다 */
interface FakeTransport {
  sent: SendMessageRequest[];
  resumed: number;
  /** resume 할 때 이미 듣는 쪽이 있었나 */
  listenedBeforeResume: boolean;
  disposed: boolean;
  retried: number;
  cancelled: number;
  decided: unknown[][];
  undone: unknown[][];
  emit: (signal: TransportSignal) => void;
}
const transports = vi.hoisted(() => [] as FakeTransport[]);
/** 보내기 결과 — 'failed' 면 접수되지 않음이 확정된 것처럼 잠금을 풉니다 */
const sendMode = vi.hoisted(() => ({ value: 'accepted' as 'accepted' | 'failed' }));
vi.mock('./chatTransport', async () => {
  const { useChatStore: store } = await import('./chatStore');
  return {
    createChatTransport: () => {
      const listeners = new Set<(signal: TransportSignal) => void>();
      const t: FakeTransport = {
        sent: [],
        resumed: 0,
        listenedBeforeResume: false,
        disposed: false,
        retried: 0,
        cancelled: 0,
        decided: [],
        undone: [],
        emit: (signal) => listeners.forEach((listener) => listener(signal)),
      };
      transports.push(t);
      return {
        send: async (draft: Parameters<ReturnType<typeof store.getState>['beginSend']>[0]) => {
          const ticket = store.getState().beginSend(draft);
          if (!ticket) return 'locked';
          t.sent.push(store.getState().pending!.request);
          if (sendMode.value === 'failed') {
            await Promise.resolve();
            store.getState().failSend(ticket);
            return 'failed';
          }
          return 'accepted';
        },
        retryUnconfirmed: async () => {
          t.retried++;
          return 'none';
        },
        resume: () => {
          t.resumed++;
          t.listenedBeforeResume = listeners.size > 0;
        },
        subscribe: (listener: (signal: TransportSignal) => void) => {
          listeners.add(listener);
          return () => listeners.delete(listener);
        },
        dispose: () => {
          t.disposed = true;
        },
        decide: async (...args: unknown[]) => {
          t.decided.push(args);
          return 'decided';
        },
        undo: async (...args: unknown[]) => {
          t.undone.push(args);
          return 'undone';
        },
        cancel: async () => {
          // 화면이 부른 횟수를 셉니다 — 잠금은 화면이 지켜야 합니다
          t.cancelled++;
          const run = store.getState().run;
          return !!run && store.getState().beginCancel(run.runId);
        },
        changeScope: async () => false,
        checkNow: () => undefined,
      };
    },
  };
});

let host: HTMLDivElement;
let root: Root;
let router: ReturnType<typeof createMemoryRouter>;
let queryClient: QueryClient;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubEnv('VITE_CHAT_AGENT', 'true');
  state.calls = 0;
  state.session = { isPending: false, isError: false, data: { id: 'u1' } };
  server.seen = [];
  server.availability = () => Promise.resolve({ enabled: true });
  server.messages = () => Promise.resolve({ messages: [], next_before_seq: null });
  server.report = (takeId) =>
    Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' });
  transports.length = 0;
  sendMode.value = 'accepted';
  sessionStorage.clear();
  useChatStore.getState().signOut();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router.dispose();
  host.remove();
  queryClient.clear();
  resetChatScreen();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

/** 조회 결과가 화면에 닿을 때까지 — TanStack Query 는 알림을 setTimeout(0) 으로 묶습니다 */
const settle = () =>
  act(async () => {
    for (let i = 0; i < 3; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  });

/** 가짜 시계에서 그만큼 흘려보냅니다 */
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
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
          { path: '/pitches', element: <p>피치 목록</p> },
          { path: '/takes', element: <p>Take 기록</p> },
          { path: '/takes/:takeId', element: <p>리포트</p> },
          { path: '/takes/:takeId/rehearsal', element: <p>리허설</p> },
          { path: '/pitch/new', element: <p>피치 만들기</p> },
          { path: '/pitch/:id/edit', element: <p>피치 수정</p> },
          { path: '/pitch/:pitchId/device-check', element: <p>장치 점검</p> },
          { path: '/welcome', element: <p>서비스 소개</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  await act(async () =>
    root.render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  );
  if (vi.isFakeTimers()) await wait(0);
  else await settle();
}

const go = async (path: string) => {
  await act(async () => router.navigate(path));
  await settle();
};
const launcher = () => host.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]');
const panel = () => host.querySelector<HTMLElement>('[role="dialog"]');
const input = () => host.querySelector('textarea')!;
const button = (label: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === label);
const sendButton = () => button('입력')!;
const sentMessages = () =>
  [...panel()!.querySelectorAll('p.self-end')].map((message) => message.textContent);
const scopeText = () => panel()!.querySelector('[data-chat-scope]')!.textContent;
const transport = () => transports.at(-1)!;
const alerts = () => [...host.querySelectorAll('[role="alert"]')].map((a) => a.textContent);

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

// ── 8-1 연결 ─────────────────────────────────────────────

/** 걸어 둔 응답 — 시험이 직접 풉니다 */
function held<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 끊기면 실패하는 걸린 요청 — 사용 가능 여부의 5초 제한을 봅니다 */
const hangUntilAborted = (signal: AbortSignal | null) =>
  new Promise<unknown>((_, reject) =>
    signal?.addEventListener('abort', () => reject(new DOMException('끊김', 'AbortError'))),
  );

const availabilityCalls = () => server.seen.filter((s) => s.path === '/api/chat/availability');

const deviceBody: ScreenBody = {
  name: 'device_check',
  camera: 'connected',
  mic: 'detected',
  gaze_setup: 'DONE',
  gaze_reason: null,
};

const claim = (text: string) =>
  ({
    index: 0,
    kind: 'FACT',
    text,
    chips: [],
    missing: 0,
    general: false,
    assessment: null,
    actionId: null,
    masked: false,
  }) as unknown as ShownClaim;

describe('사용 가능 여부 (12-4 F1 · L3-02)', () => {
  it('조회하는 동안은 숨기고, 켜짐이면 보인다', async () => {
    const answer = held<unknown>();
    server.availability = () => answer.promise;
    await visit('/');
    expect(launcher()).toBeNull();

    answer.resolve({ enabled: true });
    await settle();
    expect(launcher()).not.toBeNull();
  });

  it.each([
    ['enabled:false', () => Promise.resolve({ enabled: false })],
    ['AGENT_DISABLED', () => Promise.reject(new ApiFailure(503, 'AGENT_DISABLED'))],
  ])('%s 는 꺼짐으로 숨기고 통신 계층을 만들지 않는다', async (_, answer) => {
    server.availability = answer;
    await visit('/');
    expect(launcher()).toBeNull();
    expect(transports).toHaveLength(0);
  });

  it('5초 안에 답이 없으면 실패로 세고 2 · 4 · 8초 뒤 다시 묻는다. 모두 실패하면 버튼과 [다시 시도]를 보인다', async () => {
    vi.useFakeTimers();
    server.availability = hangUntilAborted;
    await visit('/');
    expect(availabilityCalls()).toHaveLength(1);

    // 첫 조회 5초 + 2초 → 두 번째, 5초 + 4초 → 세 번째, 5초 + 8초 → 네 번째
    const [first, second, third] = AVAILABILITY_RETRY_MS;
    await wait(AVAILABILITY_TIMEOUT_MS + first - 1);
    expect(availabilityCalls()).toHaveLength(1);
    await wait(1);
    expect(availabilityCalls()).toHaveLength(2);
    await wait(AVAILABILITY_TIMEOUT_MS + second);
    expect(availabilityCalls()).toHaveLength(3);
    await wait(AVAILABILITY_TIMEOUT_MS + third);
    expect(availabilityCalls()).toHaveLength(4);
    // 다시 묻는 동안은 실패를 꺼짐으로도, 켜짐으로도 보지 않습니다
    expect(launcher()).toBeNull();

    await wait(AVAILABILITY_TIMEOUT_MS);
    // 실패 알림이 화면에 닿도록 한 틱 더
    await wait(1);
    expect(availabilityCalls()).toHaveLength(4);
    expect(launcher()).not.toBeNull();
    expect(transports).toHaveLength(0);

    await openPanel();
    expect(alerts()).toContain('코치를 불러오지 못했으니 다시 시도해 주세요.다시 시도');
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');

    server.availability = () => Promise.resolve({ enabled: true });
    await act(async () => button('다시 시도')!.click());
    await wait(0);
    expect(alerts()).toEqual([]);
    expect(transports).toHaveLength(1);
  });

  it('통신 계층이 AGENT_DISABLED 를 받으면 버튼을 숨기고 통신 계층을 닫는다', async () => {
    await visit('/');
    await act(async () => transport().emit({ type: 'disabled' }));
    await settle();
    expect(launcher()).toBeNull();
    expect(transport().disposed).toBe(true);
  });
});

describe('연결 (12-4 F1 · F2 · F3)', () => {
  it('확인된 로그인 사용자로 저장소를 맞추고, 확인 중 · 오류에는 비우지 않는다', async () => {
    await visit('/');
    expect(useChatStore.getState().userId).toBe('u1');

    state.session = { isPending: false, isError: true, data: undefined as never };
    await go('/pitches');
    expect(useChatStore.getState().userId).toBe('u1');

    state.session = { isPending: false, isError: false, data: null };
    await go('/');
    expect(useChatStore.getState().userId).toBeNull();
  });

  it('통신 계층은 하나 — 듣기를 건 뒤 이어 가고, 화면을 옮겨도 그대로, 숨는 화면에서 닫는다', async () => {
    await visit('/');
    await go('/pitch/new');
    await go('/pitch/p1/device-check');
    expect(transports).toHaveLength(1);
    expect(transport().resumed).toBe(1);
    expect(transport().listenedBeforeResume).toBe(true);

    await go('/takes/t1/rehearsal');
    expect(transport().disposed).toBe(true);
  });

  it.each([
    ['/', '전체', null],
    ['/pitch/new', '전체', null],
    ['/takes', '전체', null],
    ['/pitch/p1/edit', '피치', 'p1'],
    ['/pitch/p1/device-check', '피치', 'p1'],
    ['/takes?pitch=p2', '피치', 'p2'],
  ])('%s 에서 열면 처음 범위는 %s', async (path, label, pitchId) => {
    await visit(path);
    await openPanel();
    expect(useChatStore.getState().scope).toEqual({ pitchId, title: null });
    expect(scopeText()).toBe(label);
  });

  it('Take 화면은 그 Take 의 피치를 알 때까지 범위 확인 중이고 보내지 않는다', async () => {
    const report = held<unknown>();
    server.report = () => report.promise;
    await visit('/takes/t1');
    await openPanel();
    expect(scopeText()).toBe('범위 확인 중');
    await type('이 연습 어땠어?');
    await press(input(), { key: 'Enter' });
    expect(transport().sent).toEqual([]);
    expect(input().value).toBe('이 연습 어땠어?');

    report.resolve({ take_id: 't1', pitch_id: 'p-9', pitch_title: '졸업 발표' });
    await settle();
    expect(scopeText()).toBe('피치: 졸업 발표');
    await press(input(), { key: 'Enter' });
    expect(transport().sent).toHaveLength(1);
    expect(transport().sent[0]).toMatchObject({
      text: '이 연습 어땠어?',
      scope_pitch_id: 'p-9',
      page: { route: '/takes/:takeId', pitch_id: 'p-9', take_id: 't1', screen: null },
    });
  });

  it('Take 를 조회하지 못하면 전체로 단정하지 않고 다시 시도를 보인다 (L3-06)', async () => {
    server.report = () => Promise.reject(new ApiFailure(404, 'NOT_FOUND'));
    await visit('/takes/t1');
    await openPanel();
    await settle();
    expect(useChatStore.getState().scope).toBeNull();
    expect(alerts()).toContain('연습 기록의 범위를 확인하지 못했으니 다시 시도해 주세요.다시 시도');

    server.report = (takeId) =>
      Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' });
    await act(async () => button('다시 시도')!.click());
    await settle();
    expect(useChatStore.getState().scope).toEqual({ pitchId: 'p-9', title: '졸업 발표' });
    expect(alerts()).toEqual([]);
  });

  it('보낼 때 지금 화면 page 와 그 순간의 화면 상태를 싣는다', async () => {
    let gaze: ScreenBody = deviceBody;
    registerChatScreen('/pitch/:pitchId/device-check', () => gaze);
    await visit('/pitch/p1/device-check');
    await openPanel();
    await type('시선 기준이 왜 실패해?');
    gaze = { ...deviceBody, gaze_setup: 'FAILED', gaze_reason: '얼굴이 보이지 않아요' };
    await press(input(), { key: 'Enter' });

    const [sent] = transport().sent;
    expect(sent!.page).toMatchObject({
      route: '/pitch/:pitchId/device-check',
      pitch_id: 'p1',
      take_id: null,
      screen: { name: 'device_check', gaze_setup: 'FAILED', gaze_reason: '얼굴이 보이지 않아요' },
    });
    expect(sent!.page.screen!.snapshot_id).toMatch(/^ss-[0-9a-f]{16}$/);
  });

  it('평가 기준 원문이 2,000자를 넘으면 보내지 않고 알린다 (L3-11)', async () => {
    registerChatScreen('/pitch/new', () => ({
      name: 'pitch_create',
      step: 'criteria',
      version_label: 'V1',
      version_id: null,
      saved: null,
      draft: {
        title: null,
        slide_count: null,
        script_chars: null,
        time_limit_sec: 300,
        criteria_text: '가'.repeat(2001),
      },
      changed: [],
    }));
    await visit('/pitch/new');
    await openPanel();
    await type('기준 괜찮아?');
    await press(input(), { key: 'Enter' });
    expect(transport().sent).toEqual([]);
    expect(alerts()).toContain('평가 기준이 2,000자를 넘어 보낼 수 없어요.');
    expect(input().value).toBe('기준 괜찮아?');
  });

  it('보내기 전에는 화면을 따라가고, 보낸 뒤에는 화면을 옮겨도 범위가 바뀌지 않는다', async () => {
    await visit('/');
    await openPanel();
    await go('/pitch/p1/edit');
    expect(useChatStore.getState().scope).toEqual({ pitchId: 'p1', title: null });

    await type('질문');
    await press(input(), { key: 'Enter' });
    await go('/');
    expect(useChatStore.getState().scope).toEqual({ pitchId: 'p1', title: null });
  });

  it('접수되지 않음이 확정되면 보낸 글을 입력에 되돌린다', async () => {
    sendMode.value = 'failed';
    await visit('/');
    await openPanel();
    await type('다시 보낼 말');
    await press(input(), { key: 'Enter' });
    await settle();
    expect(transport().sent).toHaveLength(1);
    expect(sentMessages()).toEqual([]);
    expect(input().value).toBe('다시 보낼 말');
  });

  it('입력은 사용자별 — 다른 사용자로 바뀌면 앞 사용자의 글을 보이지 않는다', async () => {
    await visit('/');
    await openPanel();
    await type('u1 의 글');
    state.session = { isPending: false, isError: false, data: { id: 'u2' } };
    await go('/pitches');
    await openPanel();
    expect(input().value).toBe('');
  });

  it('미확정이면 [다시 확인]을 보이고, 누르면 같은 요청을 다시 확인한다', async () => {
    await visit('/');
    await openPanel();
    await act(async () => transport().emit({ type: 'unconfirmed', sendId: 's1' }));
    expect(alerts()).toContain('전송 여부를 확인하지 못했으니 다시 확인해 주세요.다시 확인');
    await act(async () => button('다시 확인')!.click());
    expect(transport().retried).toBe(1);
    expect(alerts()).toEqual([]);
  });

  it('실행 중 받은 문장을 보이고, 끝나면 메시지 목록을 다시 읽어 저장된 답으로 바꾼다', async () => {
    await visit('/');
    const store = useChatStore.getState();
    store.selectConversation('c1', { pitchId: null, title: null });
    store.resolveConversation(chatTicket(), { runId: 'r1', phase: 'RUNNING' });
    await openPanel();
    await settle();
    const reads = () => server.seen.filter((s) => s.path.startsWith('/api/chat/conversations/c1'));
    const before = reads().length;

    await act(async () =>
      transport().emit({
        type: 'progress',
        runId: 'r1',
        conversationId: 'c1',
        status: 'RUNNING',
        events: [],
        claims: [claim('첫 문장')],
        restart: true,
        catchUp: true,
        contractError: null,
        plan: null,
        trace: null,
        progressDropped: false,
        cards: [],
      }),
    );
    expect(panel()!.querySelector('[data-live-run]')!.textContent).toBe('첫 문장');

    server.messages = () =>
      Promise.resolve({
        messages: [
          { message_id: 'm1', role: 'user', text: '질문', run_id: 'r1' },
          { message_id: 'm2', role: 'system', text: '확인 시간이 지나 취소했어요.', run_id: 'r1' },
        ],
        next_before_seq: null,
      });
    await act(async () =>
      transport().emit({
        type: 'ended',
        runId: 'r1',
        conversationId: 'c1',
        status: 'EXPIRED',
        errorCode: null,
        endedWhileWaiting: true,
      }),
    );
    await settle();
    expect(reads().length).toBeGreaterThan(before);
    expect(panel()!.querySelector('[data-live-run]')).toBeNull();
    expect(sentMessages()).toEqual(['질문']);
    expect(panel()!.textContent).toContain('확인 시간이 지나 취소했어요.');
  });

  it('숨는 화면으로 가면 저장소의 열림도 닫는다', async () => {
    await visit('/');
    await openPanel();
    expect(useChatStore.getState().open).toBe(true);
    await go('/takes/t1/rehearsal');
    expect(useChatStore.getState().open).toBe(false);
  });
});

// ── 8-2 답 그리기 ─────────────────────────────────────────

describe('답 그리기 (12-4 F3 · F12)', () => {
  const progressOf = (
    runId: string,
    texts: string[],
    over: Partial<Extract<TransportSignal, { type: 'progress' }>> = {},
  ): TransportSignal => ({
    type: 'progress',
    runId,
    conversationId: 'c1',
    status: 'RUNNING',
    events: [],
    claims: texts.map((text, index) => ({ ...claim(text), index })),
    restart: false,
    catchUp: false,
    contractError: null,
    plan: null,
    trace: null,
    progressDropped: false,
    cards: [],
    ...over,
  });

  async function watching(runId: string, phase: 'QUEUED' | 'RUNNING' = 'RUNNING') {
    await visit('/');
    const store = useChatStore.getState();
    store.selectConversation('c1', { pitchId: null, title: null });
    store.resolveConversation(chatTicket(), { runId, phase });
    await openPanel();
    await settle();
  }

  const live = () => panel()!.querySelector('[data-live-run]');

  it('기다리는 동안 "기록을 확인하고 있어요" 와 [취소] — 누르면 취소하고 확정될 때까지 잠근다', async () => {
    await watching('r1', 'QUEUED');
    const waiting = () => panel()!.querySelector('[data-run-waiting]');
    expect(waiting()!.textContent).toContain('기록을 확인하고 있어요');

    await act(async () => button('취소')!.click());
    expect(transport().cancelled).toBe(1);
    const locked = button('취소 중')!;
    expect(locked.getAttribute('aria-disabled')).toBe('true');
    await act(async () => locked.click());
    expect(transport().cancelled).toBe(1);

    // 끝나면 사라집니다
    await act(async () => useChatStore.getState().endRun('r1'));
    expect(waiting()).toBeNull();
  });

  it('승인 대기에서는 기다림 줄을 보이지 않는다 — 확인 카드가 대신한다', async () => {
    await watching('r1');
    await act(async () => useChatStore.getState().setRunPhase('r1', 'WAITING_APPROVAL'));
    expect(panel()!.querySelector('[data-run-waiting]')).toBeNull();
  });

  it('이 화면에서 보낸 실행의 문장은 글자 효과로 보인다', async () => {
    await watching('r1');
    await act(async () => transport().emit(progressOf('r1', ['첫 문장입니다'], { restart: true })));
    // 효과 — 아직 다 보이지 않았습니다
    expect(live()!.textContent).not.toBe('첫 문장입니다');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 300)));
    expect(live()!.textContent).toBe('첫 문장입니다');
  });

  it('따라잡기(새로고침 · 다시 열기)는 효과 없이 곧바로 보인다', async () => {
    await watching('r1');
    await act(async () =>
      transport().emit(progressOf('r1', ['이미 만든 문장'], { restart: true, catchUp: true })),
    );
    expect(live()!.textContent).toBe('이미 만든 문장');
  });

  it('다른 실행의 진행이 오면 앞 실행의 문장을 비우고 새로 그린다', async () => {
    await watching('r1');
    await act(async () =>
      transport().emit(progressOf('r1', ['앞 실행'], { restart: true, catchUp: true })),
    );
    await act(async () =>
      // 다른 실행의 첫 신호가 처음부터 다시 보냄(restart)이 아니어도 앞 실행의 문장을 남기지 않습니다 —
      // 같은 순번(0)이라 남겨 두면 새 문장이 이미 본 순번으로 버려집니다
      transport().emit(progressOf('r2', ['새 실행'], { catchUp: true })),
    );
    expect(live()!.textContent).toBe('새 실행');
    expect(live()!.getAttribute('data-live-run')).toBe('r2');
  });

  it('그 실행의 답이 목록에 먼저 들어오면 목록 쪽 하나만 보인다', async () => {
    await watching('r1');
    await act(async () =>
      transport().emit(progressOf('r1', ['저장된 문장'], { restart: true, catchUp: true })),
    );
    server.messages = () =>
      Promise.resolve({
        messages: [
          {
            message_id: 'm2',
            role: 'system',
            text: '저장된 문장',
            run_id: 'r1',
          },
          {
            message_id: 'm3',
            role: 'assistant',
            status: 'PARTIAL',
            text: '',
            run_id: 'r1',
            claims: [],
            cards: [],
            masked: false,
          },
        ],
        next_before_seq: null,
      });
    await act(async () => queryClient.invalidateQueries());
    await settle();
    expect(live()).toBeNull();
  });

  it('끝나면 남은 글자를 다 보인 뒤 목록을 다시 읽어 저장된 답 하나만 남긴다', async () => {
    await watching('r1');
    await act(async () =>
      transport().emit(
        progressOf('r1', ['아주 긴 문장이 아직 다 보이지 않았어요'], { restart: true }),
      ),
    );
    const answer = held<unknown>();
    server.messages = () => answer.promise;
    await act(async () =>
      transport().emit({
        type: 'ended',
        runId: 'r1',
        conversationId: 'c1',
        status: 'DONE',
        errorCode: null,
        endedWhileWaiting: false,
      }),
    );
    // 목록을 읽는 동안에도 받은 답은 다 보입니다 — 비었다가 다시 나타나지 않게
    expect(live()!.textContent).toBe('아주 긴 문장이 아직 다 보이지 않았어요');

    answer.resolve({
      messages: [
        { message_id: 'm1', role: 'user', text: '질문', run_id: 'r1' },
        {
          message_id: 'm2',
          role: 'assistant',
          status: 'DONE',
          text: '아주 긴 문장이 아직 다 보이지 않았어요',
          run_id: 'r1',
          claims: [
            {
              kind: 'GENERAL',
              text: '아주 긴 문장이 아직 다 보이지 않았어요',
              evidence_ids: [],
              source_ids: [],
              assessment_id: null,
              action_id: null,
            },
          ],
          cards: [],
          evidence: [],
          sources: [],
          assessments: [],
          plan: null,
          trace: null,
          masked: false,
        },
      ],
      next_before_seq: null,
    });
    await settle();
    expect(live()).toBeNull();
    expect(panel()!.querySelectorAll('[data-claim]')).toHaveLength(1);
    expect(panel()!.querySelector('[data-message="m2"]')!.textContent).toContain(
      '아주 긴 문장이 아직 다 보이지 않았어요',
    );
  });
});

// ── 8-3 카드 ─────────────────────────────────────────────

describe('카드 연결 (12-4 F6 · F10)', () => {
  const PITCH = '22222222-2222-4222-8222-222222222222';
  const goal = {
    metric: 'audience_ratio',
    slide_number: 3,
    operator: 'gt_base',
    base: { take_id: 't', take_number: 2, value: 0.31, range: null },
  };
  const assistant = (id: string, runId: string, status: string, cards: unknown[]) => ({
    message_id: id,
    role: 'assistant',
    status,
    text: '확인했어요.',
    run_id: runId,
    claims: [
      {
        kind: 'GENERAL',
        text: '확인했어요.',
        evidence_ids: [],
        source_ids: [],
        assessment_id: null,
        action_id: null,
      },
    ],
    cards,
    evidence: [],
    sources: [],
    assessments: [],
    plan: null,
    trace: null,
    masked: false,
  });
  const proposal = {
    type: 'GOAL_PROPOSAL',
    proposal_id: 'p-1',
    options: [
      {
        label: 'EXPERT',
        goal: { ...goal, version: 1 },
        reason: '추천',
        evidence_ids: [],
        content_hash: 'h',
      },
    ],
  };

  async function inConversation(messages: unknown[]) {
    server.messages = () => Promise.resolve({ messages, next_before_seq: null });
    await visit('/');
    const store = useChatStore.getState();
    store.selectConversation('c1', { pitchId: null, title: null });
    store.resolveConversation(chatTicket(), null);
    await openPanel();
    await settle();
  }

  it('[이 안으로]는 "{안}으로 목표를 잡아 줘" 와 choice 를 함께 보낸다', async () => {
    await inConversation([assistant('m1', 'r1', 'DONE', [proposal])]);
    await act(async () => button('이 안으로')!.click());
    expect(transport().sent).toHaveLength(1);
    expect(transport().sent[0]).toMatchObject({
      text: '추천 안으로 목표를 잡아 줘',
      choice: { proposal_id: 'p-1', option: 'EXPERT' },
      conversation_id: 'c1',
    });
  });

  it('만료된 실행의 제안은 고를 수 없다', async () => {
    await inConversation([assistant('m1', 'r1', 'EXPIRED', [proposal])]);
    expect(button('이 안으로')).toBeUndefined();
    expect(panel()!.textContent).toContain('시간이 지나 다시 물어봐 주세요.');
  });

  it('[되돌리기]는 그 메시지의 실행 · 행동으로 보내고, 되돌리면 목록을 다시 읽는다', async () => {
    await inConversation([
      assistant('m1', 'r1', 'DONE', [
        {
          type: 'GOAL_SAVED',
          action_id: 'a-1',
          goal: { ...goal, version: 1 },
          reason: '',
          evidence_ids: [],
          undo: true,
        },
      ]),
    ]);
    const reads = () =>
      server.seen.filter((x) => x.path.startsWith('/api/chat/conversations/c1')).length;
    const before = reads();
    await act(async () => button('되돌리기')!.click());
    await settle();
    expect(transport().undone).toEqual([['r1', 'a-1']]);
    expect(reads()).toBeGreaterThan(before);
  });

  it('승인 대기 카드는 문장이 다 보인 뒤에 달고, [승인]은 카드의 args_hash 로 보낸다', async () => {
    await visit('/');
    const store = useChatStore.getState();
    store.selectConversation('c1', { pitchId: null, title: null });
    store.resolveConversation(chatTicket(), { runId: 'r1', phase: 'WAITING_APPROVAL' });
    await openPanel();
    await settle();
    const ask = readCard(
      {
        type: 'ASK_CONFIRM',
        action_id: 'a-9',
        action_type: 'SAVE_GOAL',
        title: '목표를 저장할까요?',
        summary: '3장 청중 응시',
        before: null,
        after: { metric: 'audience_ratio' },
        notes: [],
        expires_at: '2026-11-06T05:30:00Z',
        args_hash: 'sha-1',
      },
      emptyBasis(),
    );
    await act(async () =>
      transport().emit({
        type: 'progress',
        runId: 'r1',
        conversationId: 'c1',
        status: 'WAITING_APPROVAL',
        events: [],
        claims: [{ ...claim('먼저 판단을 보여 드릴게요.'), index: 0 }],
        restart: true,
        catchUp: false,
        contractError: null,
        plan: null,
        trace: null,
        progressDropped: false,
        cards: [ask],
      }),
    );
    // 문장이 아직 다 보이지 않았습니다
    expect(button('승인')).toBeUndefined();
    await act(async () => new Promise((resolve) => setTimeout(resolve, 600)));
    await act(async () => button('승인')!.click());
    expect(transport().decided).toEqual([['a-9', 'APPROVE', 'sha-1']]);
  });

  it('[연습 준비로]는 장치 점검 화면으로 옮기고 대화창은 열린 채로 남는다', async () => {
    await inConversation([
      assistant('m1', 'r1', 'DONE', [{ type: 'START_PRACTICE', pitch_id: PITCH }]),
    ]);
    await act(async () => button('연습 준비로')!.click());
    await settle();
    expect(router.state.location.pathname).toBe(`/pitch/${PITCH}/device-check`);
    expect(panel()).not.toBeNull();
    expect(panel()!.textContent).toContain('연습 준비 화면으로 옮겼어요.');
  });
});
