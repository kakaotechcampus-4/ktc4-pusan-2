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
import { CHAT_FAILED_TEXT } from './ChatErrorBoundary';
import { AVAILABILITY_RETRY_MS, AVAILABILITY_TIMEOUT_MS } from './chatAvailability';
import { ASK_HELP_ABILITIES, ASK_HELP_EXAMPLES, ASK_HELP_TITLE } from './askHelpText';
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
  messages: (_path?: string): Promise<unknown> =>
    Promise.resolve({ messages: [], next_before_seq: null }),
  conversations: (_path?: string): Promise<unknown> =>
    Promise.resolve({ conversations: [], next_cursor: null }),
  memory: (): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
  pitches: (): Promise<unknown> => Promise.resolve({ pitches: [] }),
  removeMemory: (): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
  resources: (_pitchId: string): Promise<unknown> => Promise.reject(new Error('버전 목록 없음')),
  report: (takeId: string): Promise<unknown> =>
    Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' }),
}));
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  apiRequest: (path: string, init: RequestInit = {}) => {
    server.seen.push({ path, signal: init.signal ?? null });
    if (path === '/api/chat/availability') return server.availability(init.signal ?? null);
    if (path.startsWith('/api/chat/conversations/')) return server.messages(path);
    if (path.split('?')[0] === '/api/chat/conversations') return server.conversations(path);
    if (path === '/api/chat/memory') return server.memory();
    if (path.startsWith('/api/chat/memory/items/')) return server.removeMemory();
    if (path === '/api/pitches/') return server.pitches();
    const resources = /^\/api\/pitches\/([^/]+)\/resources$/.exec(path);
    if (resources) return server.resources(resources[1]!);
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
  scopes: unknown[];
  checks: number;
  emit: (signal: TransportSignal) => void;
}
const transports = vi.hoisted(() => [] as FakeTransport[]);
/** 범위 바꾸기 결과 — 'missing' 이면 서버가 그 피치를 찾지 못함(404) */
const scopeMode = vi.hoisted(() => ({ value: 'ok' as 'ok' | 'missing' }));
/** 보내기 결과 — 'failed' 면 접수되지 않음이 확정된 것처럼 잠금을 풉니다 */
const undoMode = vi.hoisted(() => ({ value: 'undone' as 'undone' | 'conflict' }));
const sendMode = vi.hoisted(() => ({
  value: 'accepted' as 'accepted' | 'failed' | 'conflict',
}));
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
        scopes: [],
        checks: 0,
        emit: (signal) => listeners.forEach((listener) => listener(signal)),
      };
      transports.push(t);
      return {
        send: async (draft: Parameters<ReturnType<typeof store.getState>['beginSend']>[0]) => {
          const ticket = store.getState().beginSend(draft);
          if (!ticket) return 'locked';
          t.sent.push(store.getState().pending!.request);
          if (sendMode.value === 'conflict') {
            await Promise.resolve();
            store.getState().conflictSend(ticket);
            return 'conflict';
          }
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
          return undoMode.value;
        },
        cancel: async () => {
          // 화면이 부른 횟수를 셉니다 — 잠금은 화면이 지켜야 합니다
          t.cancelled++;
          const run = store.getState().run;
          return !!run && store.getState().beginCancel(run.runId);
        },
        // 범위 바꾸기 — 새 대화는 칩만, 있는 대화는 서버가 받아들인 것처럼(또는 거절)
        changeScope: async (scope: { pitchId: string | null; title: string | null }) => {
          t.scopes.push(scope);
          const st = store.getState();
          if (st.conversationId === null) return st.chooseScope(scope);
          const ticket = st.beginScopeChange(scope);
          if (!ticket) return false;
          await Promise.resolve();
          if (scopeMode.value === 'missing') {
            store.getState().finishScopeChange(ticket, null);
            t.emit({ type: 'scopeFailed', code: 'NOT_FOUND' });
            return false;
          }
          store.getState().finishScopeChange(ticket, scope);
          return true;
        },
        checkNow: () => {
          t.checks++;
        },
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
  state.calls = 0;
  state.session = { isPending: false, isError: false, data: { id: 'u1' } };
  server.seen = [];
  server.availability = () => Promise.resolve({ enabled: true });
  server.messages = () => Promise.resolve({ messages: [], next_before_seq: null });
  server.conversations = () => Promise.resolve({ conversations: [] });
  server.memory = () => Promise.resolve({ enabled: true, items: [] });
  server.pitches = () => Promise.resolve({ pitches: [] });
  server.resources = () => Promise.reject(new Error('버전 목록 없음'));
  scopeMode.value = 'ok';
  server.report = (takeId) =>
    Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' });
  transports.length = 0;
  sendMode.value = 'accepted';
  undoMode.value = 'undone';
  sessionStorage.clear();
  localStorage.clear();
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
            <div className="chat-layout">
              <div className="chat-layout-content">
                <Outlet />
              </div>
              <ChatAgentHost />
            </div>
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
          {
            path: '/pitch/:pitchId/device-check',
            element: (
              <main id="main" tabIndex={-1}>
                장치 점검
              </main>
            ),
          },
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
/** 답 말풍선의 답 글 — 문장(`[data-claim]`)만 이어 읽습니다([건너뛰기] 버튼 글자는 빼고) */
const answerText = (bubble: Element | null) =>
  [...(bubble?.querySelectorAll('[data-claim]') ?? [])].map((c) => c.textContent).join('');
const input = () => host.querySelector('textarea')!;
/** 버튼의 보이는 글 — 화면 읽기 프로그램에만 주는 잠긴 이유(sr-only)는 뺍니다 */
const visibleText = (el: Element) => {
  const copy = el.cloneNode(true) as Element;
  copy.querySelectorAll('.sr-only').forEach((s) => s.remove());
  return copy.textContent;
};
const button = (label: string) =>
  [...host.querySelectorAll('button')].find(
    (b) => visibleText(b) === label || b.getAttribute('aria-label') === label,
  );
const sendButton = () => panel()!.querySelector<HTMLButtonElement>('button[type="submit"]')!;
const listNewButton = () =>
  panel()!.querySelector<HTMLButtonElement>('[data-conversation-list] > button')!;
const sentMessages = () =>
  [...panel()!.querySelectorAll('p.self-end')].map((message) => message.textContent);
const scopeText = () => visibleText(panel()!.querySelector('[data-chat-scope]')!);
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

it('새미나이 브랜드와 빈 대화 안내를 유지하고, 추천 질문은 별도 PR로 분리한다', async () => {
  await visit('/');
  await openPanel();
  expect(panel()!.textContent).toContain('새미나이');
  expect(launcher()!.getAttribute('aria-label')).toBe('새미나이 열기');
  expect(launcher()!.getAttribute('title')).toBe('새미나이 열기');
  const labelId = panel()!.getAttribute('aria-labelledby')!;
  expect(document.getElementById(labelId)!.textContent!.trim()).toBe('새미나이');
  expect(button('새미나이 닫기')?.getAttribute('title')).toBe('새미나이 닫기');
  expect(panel()!.querySelectorAll('img[src="/semini.png"]')).toHaveLength(2);
  expect(panel()!.querySelector('[data-chat-empty]')!.textContent).toContain(
    '발표 기록을 분석하거나 다음 연습을 준비할 수 있어요.',
  );
  expect(panel()!.querySelector('[data-chat-empty] button')).toBeNull();
  expect(sentMessages()).toEqual([]);
});

it('좁은 패널에서 제목과 아이콘 버튼을 줄바꿈할 수 있도록 헤더 구조를 유지한다', async () => {
  await visit('/');
  await openPanel();
  const firstRow = panel()!.querySelector('header > div')!;
  expect(firstRow.classList.contains('flex-wrap')).toBe(true);
  expect(firstRow.firstElementChild!.classList.contains('min-w-32')).toBe(true);
  expect(firstRow.lastElementChild!.classList.contains('ml-auto')).toBe(true);
});

it('첫 기억 안내와 빈 대화는 스크롤 맨 위에서 시작하고, 실제 전송에는 아래를 따라간다', async () => {
  await visit('/');
  await openPanel();
  const scroller = panel()!.querySelector<HTMLElement>('[aria-label="채팅 메시지"]')!;
  expect(scroller.scrollTop).toBe(0);
  expect(scroller.querySelector('[data-memory-intro]')).not.toBeNull();
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 500 });
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 100 });
  // 빈 화면에 내용이 바뀌어도 기억 안내를 밀어 내리지 않습니다.
  await act(async () => {
    scroller.querySelector('[data-chat-empty] p')!.textContent = '새미나이에게 물어보세요.';
  });
  await settle();
  expect(scroller.scrollTop).toBe(0);
});

it('대화 목록에서도 전송 확인 오류가 노출되고 같은 요청을 다시 확인할 수 있다', async () => {
  await visit('/');
  await openPanel();
  await act(async () => button('대화 목록')!.click());
  await settle();
  expect(panel()!.querySelector('form')!.hidden).toBe(true);

  await act(async () =>
    transport().emit({ type: 'unconfirmed', sendId: 's1', notAccepted: false }),
  );
  const notices = panel()!.querySelector('[data-chat-notices]')!;
  expect(notices.textContent).toContain('전송 여부를 확인하지 못했으니 다시 확인해 주세요.');
  expect(alerts()).toContain('전송 여부를 확인하지 못했으니 다시 확인해 주세요.다시 확인');
  await act(async () => button('대화 목록')!.click());
  expect(panel()!.querySelector('[data-chat-notices]')).toBe(notices);
  expect(alerts()).toHaveLength(1);
  await act(async () => button('대화 목록')!.click());
  await act(async () => button('다시 확인')!.click());
  expect(transport().retried).toBe(1);
  expect(panel()!.querySelector('[data-chat-notices]')).toBeNull();
});

it('목록 화면에서도 범위 확인 실패 안내를 보고 재시도할 수 있다', async () => {
  server.report = () => Promise.reject(new ApiFailure(404, 'NOT_FOUND'));
  await visit('/takes/t1');
  await openPanel();
  await settle();
  await act(async () => button('대화 목록')!.click());
  await settle();

  expect(panel()!.querySelector('form')!.hidden).toBe(true);
  expect(panel()!.querySelector('[data-chat-notices]')!.textContent).toContain(
    '연습 기록의 범위를 확인하지 못했으니 다시 시도해 주세요.',
  );
  server.report = (takeId) =>
    Promise.resolve({ take_id: takeId, pitch_id: 'p-9', pitch_title: '졸업 발표' });
  await act(async () => button('다시 시도')!.click());
  await settle();
  expect(panel()!.querySelector('[data-chat-notices]')).toBeNull();
});

it('대화 목록에서는 입력 폼을 숨기고 돌아오면 입력을 유지한다', async () => {
  await visit('/');
  await openPanel();
  await type('작성 중');
  await act(async () => button('대화 목록')!.click());
  expect(panel()!.querySelector('form')!.hidden).toBe(true);
  await act(async () => button('대화 목록')!.click());
  expect(panel()!.querySelector('form')!.hidden).toBe(false);
  expect(input().value).toBe('작성 중');
});

it('런처는 왼쪽 아래에 두고 패널은 화면 오른쪽에 연다', async () => {
  await visit('/');
  const container = host.querySelector<HTMLElement>('[data-chat-launcher]')!;
  expect(container.classList.contains('left-2')).toBe(true);
  expect(container.style.getPropertyValue('--chat-launcher-default-bottom')).toBe('76px');
  expect(container.style.getPropertyValue('--chat-launcher-bottom')).toBe('');

  await openPanel();
  expect(panel()!.classList.contains('right-0')).toBe(true);
});

it('런처로 열면 입력칸에 초점이 가고, 닫으면 런처로 돌아온다', async () => {
  await visit('/');
  await openPanel();
  expect(panel()).not.toBeNull();
  expect(launcher()!.getAttribute('aria-expanded')).toBe('true');
  expect(document.activeElement).toBe(input());

  const close = button('새미나이 닫기')!;
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

it('대화창이 렌더 중에 던져도 페이지는 그대로이고, [다시 시도]는 처음엔 닫기만 · 되풀이되면 새 대화로 (L3-36)', async () => {
  const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const silence = (event: ErrorEvent) => event.preventDefault();
  window.addEventListener('error', silence);
  const ok = state.session;
  /** 세션 훅이 던지게 하고 화면을 옮겨 대화창을 다시 그립니다 */
  const crash = (path: string) => {
    state.session = {
      ...ok,
      get data(): { id: string } {
        throw new Error('session boom');
      },
    };
    return go(path);
  };
  const retry = async () => {
    state.session = ok;
    await act(async () => button('다시 시도')!.click());
    await settle();
  };
  try {
    await visit('/takes/t1');
    await act(async () =>
      useChatStore.getState().selectConversation('c-1', { pitchId: null, title: null }),
    );
    await settle();
    await openPanel();
    input().focus();
    await crash('/takes/t2');

    expect(alerts()).toEqual([CHAT_FAILED_TEXT]);
    expect(launcher()).toBeNull();
    expect(host.textContent).toContain('리포트');
    // 입력칸이 사라졌으니 초점은 body 가 아니라 [다시 시도]로 갑니다
    expect(document.activeElement).toBe(button('다시 시도'));

    // 첫 실패: 대화창만 닫고 보던 대화는 그대로 둡니다
    await retry();
    expect(alerts()).not.toContain(CHAT_FAILED_TEXT);
    expect(panel()).toBeNull();
    expect(launcher()!.getAttribute('aria-expanded')).toBe('false');
    expect(useChatStore.getState().conversationId).toBe('c-1');
    expect(document.activeElement).toBe(launcher());

    // 이 페이지에서 두 번째 실패: [새 대화]처럼 보던 대화에서 나옵니다
    await openPanel();
    await crash('/takes/t3');
    expect(alerts()).toEqual([CHAT_FAILED_TEXT]);
    await retry();
    expect(panel()).toBeNull();
    expect(useChatStore.getState().conversationId).toBeNull();
    expect(document.activeElement).toBe(launcher());
  } finally {
    window.removeEventListener('error', silence);
    quiet.mockRestore();
  }
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

it('열린 패널은 허용 화면에서 유지되고 장치 점검에 들어가면 닫힌다', async () => {
  await visit('/');
  await openPanel();
  await type('연습 준비 중');
  await go('/pitch/new');
  expect(panel()).not.toBeNull();
  expect(input().value).toBe('연습 준비 중');
  await go('/pitch/p1/device-check');
  expect(launcher()).toBeNull();
  expect(panel()).toBeNull();
  await go('/pitch/new');
  expect(panel()).toBeNull();
  await openPanel();
  expect(input().value).toBe('연습 준비 중');

  await go('/takes/t1/rehearsal');
  expect(launcher()).toBeNull();
  expect(panel()).toBeNull();
});

it('장치 점검으로 이동하면 사라진 채팅 버튼 대신 화면 본문에 초점을 준다', async () => {
  await visit('/');
  await openPanel();
  input().focus();
  await go('/pitch/p1/device-check');
  expect(document.activeElement).toBe(host.querySelector('main#main'));
});

it('지연 로딩 중의 본문이 교체되면 초점을 다시 옮기되 다른 조작부의 초점은 존중한다', async () => {
  await visit('/');
  await openPanel();
  input().focus();
  await go('/pitch/p1/device-check');
  const fallback = host.querySelector<HTMLElement>('main#main')!;
  expect(document.activeElement).toBe(fallback);

  const loaded = document.createElement('main');
  loaded.id = 'main';
  loaded.tabIndex = -1;
  loaded.textContent = '장치 점검을 불러왔어요';
  await act(async () => {
    fallback.replaceWith(loaded);
    await Promise.resolve();
  });
  expect(document.activeElement).toBe(loaded);

  const anotherButton = document.createElement('button');
  host.append(anotherButton);
  anotherButton.focus();
  const next = document.createElement('main');
  next.id = 'main';
  next.tabIndex = -1;
  await act(async () => {
    loaded.replaceWith(next);
    await Promise.resolve();
  });
  expect(document.activeElement).toBe(anotherButton);
});

it('모바일 오버레이는 배경을 비활성화하고 닫으면 복원한다', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(max-width: 760px)',
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  await visit('/');
  await openPanel();
  const content = host.querySelector('.chat-layout-content')!;
  expect(panel()!.getAttribute('aria-modal')).toBe('true');
  expect(content.hasAttribute('inert')).toBe(true);
  expect(document.body.style.position).toBe('fixed');
  await act(async () => button('새미나이 닫기')!.click());
  expect(panel()).toBeNull();
  expect(content.hasAttribute('inert')).toBe(false);
  expect(document.body.style.position).toBe('');
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

it('입력창의 초점 링을 유지하고 2,000자 초과 상태를 테두리에도 표시한다', async () => {
  await visit('/');
  await openPanel();
  const composer = () => panel()!.querySelector<HTMLElement>('[data-chat-composer]')!;
  expect(composer().classList.contains('focus-within:ring-2')).toBe(true);
  expect(composer().classList.contains('focus-within:ring-coral')).toBe(true);
  expect(composer().classList.contains('border-line-strong')).toBe(true);

  await type('a'.repeat(2001));
  expect(input().getAttribute('aria-invalid')).toBe('true');
  expect(composer().classList.contains('border-coral-deep')).toBe(true);
  expect(composer().classList.contains('focus-within:ring-coral-deep')).toBe(true);
  expect(composer().classList.contains('focus-within:ring-coral')).toBe(false);

  await type('수정한 질문');
  expect(input().getAttribute('aria-invalid')).toBe('false');
  expect(composer().classList.contains('border-line-strong')).toBe(true);
  expect(composer().classList.contains('focus-within:ring-coral')).toBe(true);
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

const pitchBody = {
  name: 'pitch_edit',
  step: 'criteria',
  version_label: 'V1',
  version_id: null,
  saved: null,
  draft: {
    title: null,
    slide_count: null,
    script_chars: null,
    time_limit_sec: null,
    criteria_text: null,
  },
  changed: [],
} satisfies ScreenBody;

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
    expect(alerts()).toContain('새미나이를 불러오지 못했으니 다시 시도해 주세요.다시 시도');
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
    await go('/pitches');
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
    let screen: ScreenBody = pitchBody;
    registerChatScreen('/pitch/:id/edit', () => screen);
    await visit('/pitch/p1/edit');
    await openPanel();
    await type('발표 자료를 확인해 줘');
    screen = { ...pitchBody, version_label: 'V2' };
    await press(input(), { key: 'Enter' });

    const [sent] = transport().sent;
    expect(sent!.page).toMatchObject({
      route: '/pitch/:id/edit',
      pitch_id: 'p1',
      take_id: null,
      screen: { name: 'pitch_edit', version_label: 'V2' },
    });
    expect(sent!.page.screen!.snapshot_id).toMatch(/^ss-[0-9a-f]{16}$/);
  });

  it('처음 확인(없음 → 사용자) 뒤에 보내면 먼저 등록한 화면 상태를 싣는다', async () => {
    expect(useChatStore.getState().userId).toBeNull();
    registerChatScreen('/pitch/:id/edit', () => pitchBody);
    await visit('/pitch/p1/edit');
    expect(useChatStore.getState().userId).toBe('u1');
    await openPanel();
    await type('자료 괜찮아?');
    await press(input(), { key: 'Enter' });
    expect(transport().sent[0]!.page.screen).toMatchObject({ name: 'pitch_edit' });
  });

  it('로그아웃 신호 없이 다른 사용자로 바뀐 뒤 보내면 앞 사람 때 등록한 화면 상태를 싣지 않는다', async () => {
    registerChatScreen('/pitch/:id/edit', () => pitchBody);
    await visit('/pitch/p1/edit');
    expect(useChatStore.getState().userId).toBe('u1');

    // 같은 경로 패턴에 머문 채 사용자만 바뀝니다 — 경로가 달라서 빠지는 것이 아님을 보려고 패턴을 유지합니다
    state.session = { isPending: false, isError: false, data: { id: 'u2' } };
    await go('/pitch/p2/edit');
    expect(useChatStore.getState().userId).toBe('u2');
    await openPanel();
    await type('자료 괜찮아?');
    await press(input(), { key: 'Enter' });
    const [sent] = transport().sent;
    expect(sent!.page).toMatchObject({ route: '/pitch/:id/edit', screen: null });
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

  it('화면 상태를 만들지 못하면 보내지 않고 다시 시도하라고 알린다 (L3-10)', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    registerChatScreen('/pitch/new', () => {
      throw new Error('화면 쪽 실수');
    });
    await visit('/pitch/new');
    await openPanel();
    await type('이 대본 괜찮아?');
    await press(input(), { key: 'Enter' });
    expect(transport().sent).toEqual([]);
    expect(alerts()).toContain('현재 입력 상태를 확인하지 못했으니 다시 시도해 주세요.');
    expect(input().value).toBe('이 대본 괜찮아?');
    errors.mockRestore();
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

  it('[다시 확인] · [다시 보내기] 뒤에 접수되지 않음이 확정돼도 보낸 글을 입력에 되돌린다', async () => {
    await visit('/');
    await openPanel();
    await type('미확정이 된 말');
    await press(input(), { key: 'Enter' });
    await settle();
    expect(input().value).toBe('');
    // 다시 확인했더니 4xx — 통신 계층이 그 보내기를 놓습니다
    const sendId = useChatStore.getState().pending!.sendId;
    await act(async () => useChatStore.getState().failSend({ ...chatTicket(), sendId }));
    expect(input().value).toBe('미확정이 된 말');
  });

  it('되돌릴 때 그사이 새로 쓴 글이 있으면 두고, 다른 탭 실행과 부딪혀도 되돌린다', async () => {
    await visit('/');
    await openPanel();
    await type('먼저 보낸 말');
    await press(input(), { key: 'Enter' });
    await settle();
    await type('새로 쓰는 말');
    let sendId = useChatStore.getState().pending!.sendId;
    await act(async () => useChatStore.getState().failSend({ ...chatTicket(), sendId }));
    expect(input().value).toBe('새로 쓰는 말');

    await press(input(), { key: 'Enter' });
    await settle();
    sendId = useChatStore.getState().pending!.sendId;
    await act(async () => useChatStore.getState().conflictSend({ ...chatTicket(), sendId }));
    expect(input().value).toBe('새로 쓰는 말');
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
    await act(async () =>
      transport().emit({ type: 'unconfirmed', sendId: 's1', notAccepted: false }),
    );
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
    expect(answerText(panel()!.querySelector('[data-live-run]'))).toBe('첫 문장');

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
    expect(answerText(live())).not.toBe('첫 문장입니다');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 300)));
    expect(answerText(live())).toBe('첫 문장입니다');
  });

  it('따라잡기(새로고침 · 다시 열기)는 효과 없이 곧바로 보인다', async () => {
    await watching('r1');
    await act(async () =>
      transport().emit(progressOf('r1', ['이미 만든 문장'], { restart: true, catchUp: true })),
    );
    expect(answerText(live())).toBe('이미 만든 문장');
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
    expect(answerText(live())).toBe('새 실행');
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
    expect(answerText(live())).toBe('아주 긴 문장이 아직 다 보이지 않았어요');

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

  it('[되돌리기]가 409 면 충돌을 알리고 목록을 다시 읽어 지금 목표를 보인다 (L3-29)', async () => {
    undoMode.value = 'conflict';
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
    expect(alerts()).toContain('이미 다른 목표로 바뀌었어요.');
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

  it('[연습 준비로]는 장치 점검 화면으로 옮기고 대화창을 닫되 대화는 유지한다', async () => {
    await inConversation([
      assistant('m1', 'r1', 'DONE', [{ type: 'START_PRACTICE', pitch_id: PITCH }]),
    ]);
    await act(async () => button('연습 준비로')!.click());
    await settle();
    expect(router.state.location.pathname).toBe(`/pitch/${PITCH}/device-check`);
    expect(panel()).toBeNull();
    await go('/');
    expect(panel()).toBeNull();
    await openPanel();
    expect(button('연습 준비로')).toBeDefined();
  });
});

// ── 점검에서 찾은 문제 — 다시 생기지 않게 ──────────────────────

describe('점검 반영 (8-1 ~ 8-3)', () => {
  const progressIn = (runId: string, conversationId: string, text: string): TransportSignal => ({
    type: 'progress',
    runId,
    conversationId,
    status: 'RUNNING',
    events: [],
    claims: [{ ...claim(text), index: 0 }],
    restart: true,
    catchUp: true,
    contractError: null,
    plan: null,
    trace: null,
    progressDropped: false,
    cards: [],
  });
  const ended = (
    runId: string,
    conversationId: string,
    status: 'DONE' | 'REFUSED' = 'DONE',
  ): TransportSignal => ({
    type: 'ended',
    runId,
    conversationId,
    status,
    errorCode: null,
    endedWhileWaiting: false,
  });

  async function watching(conversationId: string, runId: string | null) {
    await visit('/');
    const s = useChatStore.getState();
    s.selectConversation(conversationId, { pitchId: null, title: null });
    s.resolveConversation(chatTicket(), runId ? { runId, phase: 'RUNNING' } : null);
    await openPanel();
    await settle();
  }

  it('접수 직후 목록을 다시 읽는 동안에도 보낸 말이 남고, 목록에 들어오면 한 번만 보인다', async () => {
    await visit('/');
    await openPanel();
    await type('질문');
    await press(input(), { key: 'Enter' });
    const list = held<unknown>();
    server.messages = () => list.promise;
    await act(async () => {
      const s = useChatStore.getState();
      s.beginRun({ ...chatTicket(), sendId: s.pending!.sendId }, 'r1', 'c1');
    });
    await settle();
    expect(sentMessages()).toEqual(['질문']);
    expect(panel()!.textContent).not.toContain('물어보세요');

    list.resolve({
      messages: [{ message_id: 'm1', role: 'user', text: '질문', run_id: 'r1' }],
      next_before_seq: null,
    });
    await settle();
    expect(sentMessages()).toEqual(['질문']);
  });

  it('지난 대화를 여는 동안에는 빈 대화 안내를 보이지 않는다', async () => {
    const list = held<unknown>();
    server.messages = () => list.promise;
    await watching('c1', null);
    expect(panel()!.textContent).not.toContain('물어보세요');
    list.resolve({
      messages: [{ message_id: 'm1', role: 'user', text: '지난 질문', run_id: 'r0' }],
      next_before_seq: null,
    });
    await settle();
    expect(sentMessages()).toEqual(['지난 질문']);
  });

  it('끝난 뒤 목록 다시 읽기가 실패하면 받은 답을 지우지 않고, 빈 대화로 보이지 않는다', async () => {
    await watching('c1', 'r1');
    await act(async () => transport().emit(progressIn('r1', 'c1', '받은 답 문장')));
    server.messages = () => Promise.reject(new Error('끊김'));
    await act(async () => transport().emit(ended('r1', 'c1')));
    await settle();
    await settle();
    expect(answerText(panel()!.querySelector('[data-live-run]'))).toBe('받은 답 문장');
    expect(panel()!.textContent).not.toContain('물어보세요');
    // 거절로 끝난 답이 아니면 질문 예시를 붙이지 않습니다
    expect(panel()!.querySelector('[data-ask-help]')).toBeNull();
  });

  it('거절로 끝난 뒤 목록 다시 읽기가 실패해도 받은 거절 답 아래에 질문 예시를 한 번 붙인다', async () => {
    await watching('c1', 'r1');
    await act(async () =>
      transport().emit(progressIn('r1', 'c1', '이 요청은 도와드릴 수 없어요.')),
    );
    // 끝나기 전에는 진행 답 아래에 붙이지 않습니다
    expect(panel()!.querySelector('[data-live-run] ~ [data-ask-help]')).toBeNull();
    server.messages = () => Promise.reject(new Error('끊김'));
    // 실제 통신 계층처럼 실행을 끝낸(endRun) 뒤 끝남 신호를 보냅니다 — 보내기 잠금이 풀립니다
    await act(async () => {
      useChatStore.getState().endRun('r1');
      transport().emit(ended('r1', 'c1', 'REFUSED'));
    });
    await settle();
    await settle();
    const helps = [...panel()!.querySelectorAll('[data-ask-help]')];
    expect(helps).toHaveLength(1);
    const liveRun = panel()!.querySelector('[data-live-run]')!;
    expect(
      liveRun.compareDocumentPosition(helps[0]!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await act(async () => helps[0]!.querySelector('button')!.click());
    expect(transport().sent.map((r) => r.text)).toEqual([ASK_HELP_EXAMPLES[0]]);
    // 보내는 중에는 안내를 거둡니다
    expect(panel()!.querySelector('[data-ask-help]')).toBeNull();
  });

  it('꺼지면 패널을 닫고, 다시 켜져도 저절로 열려 초점을 가져가지 않는다', async () => {
    await visit('/');
    await openPanel();
    await act(async () => transport().emit({ type: 'disabled' }));
    await settle();
    expect(useChatStore.getState().open).toBe(false);

    const home = button('홈 버튼')!;
    home.focus();
    await act(async () => queryClient.setQueryData(['chat', 'availability', 'u1'], true));
    await settle();
    expect(launcher()).not.toBeNull();
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(home);
  });

  it('모달이 닫혀 패널이 다시 보일 때는 초점을 가져가지 않는다 — 런처로 연 때만', async () => {
    await visit('/');
    await openPanel();
    expect(document.activeElement).toBe(input());
    await act(async () => useChatStore.getState().coverByModal());
    const home = button('홈 버튼')!;
    home.focus();
    await act(async () => useChatStore.getState().uncoverByModal());
    expect(panel()).not.toBeNull();
    expect(document.activeElement).toBe(home);
  });

  it('지난 메시지에 남은 승인 확인 카드는 "새로고침" 대체 표시로 그리지 않는다', async () => {
    server.messages = () =>
      Promise.resolve({
        messages: [
          {
            message_id: 'm1',
            role: 'assistant',
            status: 'EXPIRED',
            text: '확인할게요.',
            run_id: 'r1',
            claims: [
              {
                kind: 'GENERAL',
                text: '확인할게요.',
                evidence_ids: [],
                source_ids: [],
                assessment_id: null,
                action_id: null,
              },
            ],
            cards: [
              {
                type: 'ASK_CONFIRM',
                action_id: 'a-1',
                action_type: 'SAVE_GOAL',
                title: '목표 저장',
                summary: 's',
                before: null,
                after: {},
                notes: [],
                expires_at: '2026-11-06T05:30:00Z',
                args_hash: 'h',
              },
              { type: 'NEW_CARD' },
            ],
            masked: false,
          },
        ],
        next_before_seq: null,
      });
    await watching('c1', null);
    const cards = [...panel()!.querySelectorAll('[data-card]')];
    // 모르는 카드의 대체 표시는 그대로, 지난 승인 카드만 뺍니다
    expect(cards.map((c) => c.textContent)).toEqual([
      '이 카드를 표시할 수 없으니 새로고침해 주세요.',
    ]);
  });

  it('다른 대화로 옮기면 앞 대화의 진행 답을 비워, 돌아와도 끝난 실행의 답이 남지 않는다', async () => {
    await watching('c1', 'r1');
    await act(async () => transport().emit(progressIn('r1', 'c1', '실패하기 전 받은 문장')));
    expect(panel()!.querySelector('[data-live-run]')).not.toBeNull();
    await act(async () => {
      useChatStore.getState().selectConversation('c2', { pitchId: null, title: null });
      useChatStore.getState().resolveConversation(chatTicket(), null);
    });
    await act(async () => {
      useChatStore.getState().selectConversation('c1', { pitchId: null, title: null });
      useChatStore.getState().resolveConversation(chatTicket(), null);
    });
    await settle();
    expect(panel()!.querySelector('[data-live-run]')).toBeNull();

    // 돌아온 대화의 실행이 아직 진행 중이면 통신 계층이 처음부터 다시 보내 다시 그려집니다
    await act(async () => transport().emit(progressIn('r1', 'c1', '다시 받은 문장')));
    expect(answerText(panel()!.querySelector('[data-live-run]'))).toBe('다시 받은 문장');
  });
});

// ── 8-4 범위 칩 · 대화 목록 ─────────────────────────────────

describe('범위 칩 · 대화 목록 (12-4 F2)', () => {
  const P1 = '10000000-0000-4000-8000-000000000001';
  const P2 = '10000000-0000-4000-8000-000000000002';
  const pitchList = {
    pitches: [
      { pitch_id: P1, pitch_title: '졸업 발표', pitch_time: 300, thumbnail_url: null, takes: [] },
      { pitch_id: P2, pitch_title: '회사 소개', pitch_time: 300, thumbnail_url: null, takes: [] },
    ],
  };
  const chip = () => panel()!.querySelector<HTMLButtonElement>('[data-chat-scope]')!;
  const option = (label: string) =>
    [...panel()!.querySelectorAll<HTMLButtonElement>('[aria-label="범위 고르기"] button')].find(
      (b) => b.textContent === label,
    );

  async function inConversation(conversationId: string, run: { runId: string } | null = null) {
    const s = useChatStore.getState();
    s.selectConversation(conversationId, { pitchId: null, title: null });
    s.resolveConversation(chatTicket(), run ? { runId: run.runId, phase: 'RUNNING' } : null);
    await settle();
  }

  it('피치 화면에서 연 범위는 내 피치 목록에서 이름을 찾아 "피치: 이름" 으로 보인다', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await settle();
    expect(visibleText(chip())).toBe('피치: 졸업 발표');
    expect(chip().textContent).toBe('범위: 피치: 졸업 발표');
  });

  it('긴 피치 제목은 칩의 말줄임표와 무관하게 전체를 확인할 수 있다', async () => {
    const longTitle = '매우 긴 발표 제목을 사용하는 프로젝트의 최종 시연과 성능 평가';
    server.pitches = () =>
      Promise.resolve({
        pitches: [{ ...pitchList.pitches[0]!, pitch_title: longTitle }],
      });
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await settle();
    expect(visibleText(chip())).toBe(`피치: ${longTitle}`);
    expect(chip().getAttribute('title')).toBe(`피치: ${longTitle}`);
    // 접근성 이름은 기존 텍스트와 잠금 사유를 그대로 사용합니다.
    expect(chip().hasAttribute('aria-label')).toBe(false);
  });

  it('칩을 누르면 전체 · 내 피치에서 고르고, 고르면 그 범위로 바꾼다 — Esc 는 목록만 닫는다', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    await visit('/');
    await openPanel();
    await act(async () => chip().click());
    await settle();
    expect(chip().getAttribute('aria-expanded')).toBe('true');
    expect(option('전체')!.getAttribute('aria-pressed')).toBe('true');

    await press(option('피치: 회사 소개')!, { key: 'Escape' });
    expect(chip().getAttribute('aria-expanded')).toBe('false');
    expect(panel()).not.toBeNull();

    await act(async () => chip().click());
    // 고른 뒤 초점은 칩으로 돌아옵니다 — 다른 곳에 있던 초점에서 시작합니다
    input().focus();
    await act(async () => option('피치: 회사 소개')!.click());
    await settle();
    expect(transport().scopes).toEqual([{ pitchId: P2, title: '회사 소개' }]);
    expect(useChatStore.getState().scope).toEqual({ pitchId: P2, title: '회사 소개' });
    expect(visibleText(chip())).toBe('피치: 회사 소개');
    expect(document.activeElement).toBe(chip());
  });

  it('실행이 끝나기 전에는 칩을 막고, 막힌 동안 누른 것은 끝난 뒤에 저절로 열리지 않는다', async () => {
    await visit('/');
    await openPanel();
    await inConversation('c1', { runId: 'r1' });
    expect(chip().getAttribute('aria-disabled')).toBe('true');
    await act(async () => chip().click());
    expect(chip().getAttribute('aria-expanded')).toBe('false');
    await act(async () => useChatStore.getState().endRun('r1'));
    expect(chip().getAttribute('aria-disabled')).toBe('false');
    expect(chip().getAttribute('aria-expanded')).toBe('false');
  });

  it('펼친 범위 목록은 실행이 붙으면 닫힌다', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    await visit('/');
    await openPanel();
    await inConversation('c1');
    await act(async () => chip().click());
    expect(chip().getAttribute('aria-expanded')).toBe('true');
    await act(async () => {
      const s = useChatStore.getState();
      s.selectConversation('c1', { pitchId: null, title: null });
      useChatStore.getState().resolveConversation(chatTicket(), { runId: 'r2', phase: 'RUNNING' });
    });
    expect(chip().getAttribute('aria-expanded')).toBe('false');
    expect(panel()!.querySelector('[aria-label="범위 고르기"]')).toBeNull();
  });

  it('피치 목록을 읽지 못하면 지금 선택을 두고 다시 시도하게 한다 (L3-07)', async () => {
    server.pitches = () => Promise.reject(new Error('끊김'));
    await visit('/');
    await openPanel();
    await act(async () => chip().click());
    await settle();
    expect(alerts()).toContain('피치 목록을 불러오지 못했으니 다시 시도해 주세요.다시 시도');
    expect(useChatStore.getState().scope).toEqual({ pitchId: null, title: null });

    server.pitches = () => Promise.resolve(pitchList);
    await act(async () => button('다시 시도')!.click());
    await settle();
    expect(option('피치: 졸업 발표')).toBeDefined();
  });

  it('있는 대화의 범위 바꾸기가 404 면 앞 범위로 돌리고 "발표를 찾을 수 없으니…" 를 보인다', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    scopeMode.value = 'missing';
    await visit('/');
    await openPanel();
    await inConversation('c1');
    await act(async () => chip().click());
    await settle();
    await act(async () => option('피치: 졸업 발표')!.click());
    await settle();
    expect(useChatStore.getState().scope).toEqual({ pitchId: null, title: null });
    expect(alerts()).toContain('발표를 찾을 수 없으니 범위를 다시 선택해 주세요.');
  });

  it('보내기가 404 여도 범위의 피치가 목록에 있으면 막지 않는다 — 대화 · Take 등 다른 404 (accept.py)', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await settle();
    await act(async () => transport().emit({ type: 'sendFailed', code: 'NOT_FOUND', status: 404 }));
    await settle();
    expect(alerts()).not.toContain('발표를 찾을 수 없으니 범위를 다시 선택해 주세요.');
    await type('질문');
    expect(sendButton().getAttribute('aria-disabled')).toBe('false');
  });

  it('보내기가 범위의 피치를 찾지 못하면(404) 범위를 다시 고를 때까지 보내기를 막는다', async () => {
    server.pitches = () => Promise.resolve(pitchList);
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await settle();
    // 그사이 피치가 지워졌습니다 — 다시 읽은 목록에 없습니다
    server.pitches = () =>
      Promise.resolve({ pitches: pitchList.pitches.filter((p) => p.pitch_id !== P1) });
    await act(async () => transport().emit({ type: 'sendFailed', code: 'NOT_FOUND', status: 404 }));
    await settle();
    expect(alerts()).toContain('발표를 찾을 수 없으니 범위를 다시 선택해 주세요.');
    await type('질문');
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    await press(input(), { key: 'Enter' });
    expect(transport().sent).toEqual([]);

    await act(async () => chip().click());
    await settle();
    await act(async () => option('전체')!.click());
    await settle();
    expect(sendButton().getAttribute('aria-disabled')).toBe('false');
  });

  it('대화 목록은 제목 · 범위 · 마지막 시각 · 최신 아님을 보이고, 고르면 그 대화로 옮긴다', async () => {
    server.conversations = () =>
      Promise.resolve({
        conversations: [
          {
            conversation_id: 'c7',
            title: '3장 시선 어땠어?',
            scope_pitch_id: P1,
            scope_pitch_title: '졸업 발표',
            last_user_activity_at: '2026-11-05T12:30:00Z',
            created_at: '2026-11-05T12:00:00Z',
            stale: true,
          },
          { conversation_id: 'broken' },
        ],
      });
    await visit('/');
    await openPanel();
    await act(async () => button('대화 목록')!.click());
    await settle();
    const items = [...panel()!.querySelectorAll('[aria-label="지난 대화"] button')];
    // 깨진 줄은 빼고 나머지만 보입니다
    expect(items).toHaveLength(1);
    expect(items[0]!.textContent).toContain('3장 시선 어땠어?');
    expect(items[0]!.textContent).toContain('피치: 졸업 발표');
    expect(items[0]!.textContent).toContain('최신 아님');

    await act(async () => (items[0] as HTMLButtonElement).click());
    await settle();
    expect(useChatStore.getState().conversationId).toBe('c7');
    expect(useChatStore.getState().scope).toEqual({ pitchId: P1, title: '졸업 발표' });
    expect(panel()!.querySelector('[data-conversation-list]')).toBeNull();
  });

  it('대화를 옮기면 앞 대화의 마지막 문장을 화면 읽기 영역에 남기지 않는다', async () => {
    await visit('/');
    await openPanel();
    await inConversation('c1', { runId: 'r1' });
    await act(async () =>
      transport().emit({
        type: 'progress',
        runId: 'r1',
        conversationId: 'c1',
        status: 'RUNNING',
        events: [],
        claims: [{ ...claim('앞 대화 문장'), index: 0 }],
        restart: true,
        catchUp: false,
        contractError: null,
        plan: null,
        trace: null,
        progressDropped: false,
        cards: [],
      }),
    );
    await act(async () => new Promise((resolve) => setTimeout(resolve, 400)));
    const announce = () => host.querySelector('[data-chat-announce]')!.textContent;
    expect(announce()).toBe('앞 대화 문장');
    await act(async () => useChatStore.getState().startNewConversation(null));
    expect(announce()).toBe('');
  });

  it('[새 대화]는 지금 화면 범위의 새 대화로 옮긴다', async () => {
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await inConversation('c1');
    await act(async () => button('대화 목록')!.click());
    await settle();
    await act(async () => listNewButton().click());
    expect(useChatStore.getState().conversationId).toBeNull();
    expect(useChatStore.getState().scope).toEqual({ pitchId: P1, title: null });
  });

  it('누른 버튼이 사라지면 초점을 입력칸으로 옮겨 Esc 로 닫힌다 — [새 대화] · [다시 확인] (#818)', async () => {
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await inConversation('c1');
    await act(async () => button('대화 목록')!.click());
    await settle();
    listNewButton().focus();
    await act(async () => listNewButton().click());
    await settle();
    expect(document.activeElement).toBe(input());

    await act(async () =>
      transport().emit({ type: 'unconfirmed', sendId: 's1', notAccepted: false }),
    );
    button('다시 확인')!.focus();
    await act(async () => button('다시 확인')!.click());
    await settle();
    expect(document.activeElement).toBe(input());
    await press(document.activeElement!, { key: 'Escape' });
    expect(panel()).toBeNull();
  });

  it('사용자가 초점을 놓았으면(남아 있는 요소에서) 패널이 바뀌어도 초점을 끌어오지 않는다', async () => {
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    input().blur();
    await act(async () => button('대화 목록')!.click());
    await settle();
    expect(document.activeElement).toBe(document.body);
  });

  it('버튼에서 패널 밖으로 나간 뒤 그 버튼이 사라져도 초점을 끌어오지 않는다', async () => {
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await act(async () => button('대화 목록')!.click());
    await settle();
    listNewButton().focus();
    listNewButton().blur();
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    await act(async () => button('대화 목록')!.click());
    await settle();
    // 헤더의 새 대화 버튼은 항상 보이지만, 대화 목록 내부의 버튼은 사라집니다.
    expect(panel()!.querySelector('[data-conversation-list]')).toBeNull();
    expect(document.activeElement).toBe(document.body);
  });

  it('대화 목록을 읽지 못하면 다시 시도하게 한다', async () => {
    server.conversations = () => Promise.reject(new Error('끊김'));
    await visit('/');
    await openPanel();
    await act(async () => button('대화 목록')!.click());
    await settle();
    expect(alerts()).toContain('대화 목록을 불러오지 못했으니 다시 시도해 주세요.다시 시도');
    server.conversations = () => Promise.resolve({ conversations: [] });
    await act(async () => button('다시 시도')!.click());
    await settle();
    expect(panel()!.textContent).toContain('지난 대화가 없어요.');
  });

  describe('지난 대화 더 보기 (BE #1000 커서)', () => {
    const row = (id: string, title: string) => ({
      conversation_id: id,
      title,
      scope_pitch_id: null,
      scope_pitch_title: null,
      last_user_activity_at: '2026-11-05T12:30:00Z',
      created_at: '2026-11-05T12:00:00Z',
      stale: false,
    });
    const titles = () =>
      [...host.querySelectorAll('[aria-label="지난 대화"] li .truncate')].map((t) => t.textContent);

    it('다음 쪽이 있으면 [지난 대화 더 보기]로 받은 커서를 보내 아래에 잇고, 끝이면 버튼을 거둔다', async () => {
      server.conversations = (path) =>
        Promise.resolve(
          path?.includes('cursor=k2')
            ? // 쪽 사이에 활동이 생겨 앞 쪽의 대화가 다시 와도 한 번만 보입니다
              { conversations: [row('c2', '둘째'), row('c1', '첫째')], next_cursor: null }
            : { conversations: [row('c1', '첫째')], next_cursor: 'k2' },
        );
      await visit('/');
      await openPanel();
      await act(async () => button('대화 목록')!.click());
      await settle();
      expect(titles()).toEqual(['첫째']);

      await act(async () => button('지난 대화 더 보기')!.click());
      await settle();
      expect(server.seen.some((s) => s.path === '/api/chat/conversations?cursor=k2')).toBe(true);
      expect(titles()).toEqual(['첫째', '둘째']);
      expect(button('지난 대화 더 보기')).toBeFalsy();
    });

    it('더 보기가 실패하면 읽은 줄은 그대로 두고 그 자리에서 알리며, 다시 누르면 이어 읽는다', async () => {
      let fail = true;
      server.conversations = (path) => {
        if (!path?.includes('cursor=')) {
          return Promise.resolve({ conversations: [row('c1', '첫째')], next_cursor: 'k2' });
        }
        if (fail) return Promise.reject(new Error('끊김'));
        return Promise.resolve({ conversations: [row('c2', '둘째')], next_cursor: null });
      };
      await visit('/');
      await openPanel();
      await act(async () => button('대화 목록')!.click());
      await settle();
      await act(async () => button('지난 대화 더 보기')!.click());
      await settle();
      expect(titles()).toEqual(['첫째']);
      expect(alerts()).toContain('지난 대화를 더 불러오지 못했으니 다시 시도해 주세요.');
      expect(alerts()).not.toContain('대화 목록을 불러오지 못했으니 다시 시도해 주세요.다시 시도');

      fail = false;
      await act(async () => button('지난 대화 더 보기')!.click());
      await settle();
      expect(titles()).toEqual(['첫째', '둘째']);
      expect(alerts()).not.toContain('지난 대화를 더 불러오지 못했으니 다시 시도해 주세요.');
    });
  });

  it('[이전 대화 더 보기]는 앞 페이지를 읽어 위에 잇는다', async () => {
    const msg = (id: string, text: string) => ({ message_id: id, role: 'user', text, run_id: id });
    server.messages = (path) =>
      Promise.resolve(
        path?.includes('before_seq=11')
          ? { messages: [msg('m1', '첫 질문')], next_before_seq: null }
          : { messages: [msg('m11', '나중 질문')], next_before_seq: 11 },
      );
    await visit('/');
    await openPanel();
    await inConversation('c1');
    expect(sentMessages()).toEqual(['나중 질문']);
    await act(async () => button('이전 대화 더 보기')!.click());
    await settle();
    expect(sentMessages()).toEqual(['첫 질문', '나중 질문']);
    expect(button('이전 대화 더 보기')).toBeUndefined();
  });

  it('지난 대화를 되살리는 동안은 "이전 대화를 불러오고 있어요." — 읽지 못하면 다시 시도', async () => {
    const list = held<unknown>();
    server.messages = () => list.promise;
    await visit('/');
    await openPanel();
    await act(async () =>
      useChatStore.getState().selectConversation('c1', { pitchId: null, title: null }),
    );
    await settle();
    expect(panel()!.textContent).toContain('이전 대화를 불러오고 있어요.');

    await act(async () => useChatStore.getState().resolveConversation(chatTicket(), null));
    await act(async () => list.reject(new Error('끊김')));
    await settle();
    expect(alerts()).toContain('대화를 불러오지 못했으니 다시 시도해 주세요.다시 시도');
    expect(panel()!.textContent).not.toContain('물어보세요');
    server.messages = () =>
      Promise.resolve({
        messages: [{ message_id: 'm1', role: 'user', text: '지난 질문', run_id: 'r0' }],
        next_before_seq: null,
      });
    await act(async () => button('다시 시도')!.click());
    await settle();
    expect(sentMessages()).toEqual(['지난 질문']);
  });

  it('보던 대화가 없어지면 "대화를 찾을 수 없어요." 를 보이고 지금 화면 범위의 새 대화로 옮긴다', async () => {
    await visit(`/pitch/${P1}/edit`);
    await openPanel();
    await inConversation('c1');
    await act(async () => transport().emit({ type: 'conversationMissing', conversationId: 'c1' }));
    expect(useChatStore.getState().conversationId).toBeNull();
    expect(useChatStore.getState().scope).toEqual({ pitchId: P1, title: null });
    expect(alerts()).toContain('대화를 찾을 수 없어요.');
  });

  it('실행이 끝나면 대화 목록을 다시 읽는다 — 제목 · 시각 · 범위가 바뀌었을 수 있다', async () => {
    let reads = 0;
    server.conversations = () => {
      reads += 1;
      return Promise.resolve({ conversations: [] });
    };
    await visit('/');
    await openPanel();
    await act(async () => button('대화 목록')!.click());
    await settle();
    const before = reads;
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
    await settle();
    expect(reads).toBeGreaterThan(before);
  });
});

// ── 8-5 안내 · 자동 스크롤 ───────────────────────────────────

describe('안내 · 자동 스크롤 (12-4 F8)', () => {
  const userMsg = (id: string, text: string, runId: string) => ({
    message_id: id,
    role: 'user',
    text,
    run_id: runId,
  });
  const answer = (
    id: string,
    runId: string,
    status: string,
    texts: string[],
    plan: unknown = null,
  ) => ({
    message_id: id,
    role: 'assistant',
    status,
    text: texts.join('\n'),
    run_id: runId,
    claims: texts.map((text) => ({
      kind: 'GENERAL',
      text,
      evidence_ids: [],
      source_ids: [],
      assessment_id: null,
      action_id: null,
    })),
    cards: [],
    plan,
    trace: null,
    masked: false,
  });
  const ended = (
    runId: string,
    status: string,
    errorCode: string | null,
    endedWhileWaiting = false,
  ): TransportSignal => ({
    type: 'ended',
    runId,
    conversationId: 'c1',
    status: status as never,
    errorCode,
    endedWhileWaiting,
  });

  async function inConversation(messages: unknown[], runId: string | null = null) {
    server.messages = () => Promise.resolve({ messages, next_before_seq: null });
    await visit('/');
    await openPanel();
    const s = useChatStore.getState();
    s.selectConversation('c1', { pitchId: null, title: null });
    s.resolveConversation(chatTicket(), runId ? { runId, phase: 'RUNNING' } : null);
    await settle();
  }
  const endNotices = () =>
    [...panel()!.querySelectorAll('[data-end-notice]')].map((n) => n.textContent);

  it('실패로 끝나면 그 실행의 메시지 뒤에 안내를 붙이고, [다시 보내기]는 같은 질문을 새로 보낸다', async () => {
    await inConversation([userMsg('m1', '3장 시선 어땠어?', 'r1')], 'r1');
    await act(async () => transport().emit(ended('r1', 'FAILED', 'AGENT_TIMEOUT')));
    await act(async () => useChatStore.getState().endRun('r1'));
    await settle();
    expect(endNotices()).toEqual(['답이 늦어지고 있으니 다시 보내 주세요.다시 보내기']);
    // 안내는 그 실행의 마지막 메시지(내 말) 바로 뒤에 붙습니다
    const notice = panel()!.querySelector('[data-end-notice]')!;
    expect(notice.previousElementSibling!.textContent).toBe('3장 시선 어땠어?');

    await act(async () => button('다시 보내기')!.click());
    expect(transport().sent.map((r) => r.text)).toEqual(['3장 시선 어땠어?']);
  });

  it('대화 목록을 열었다 닫아도 지난 안내는 다시 알리지 않는다 — 글은 그대로 보인다 (오류는 한 번 알린다)', async () => {
    await inConversation([userMsg('m1', '3장 시선 어땠어?', 'r1')], 'r1');
    await act(async () => transport().emit(ended('r1', 'FAILED', 'AGENT_TIMEOUT')));
    await act(async () => useChatStore.getState().endRun('r1'));
    await settle();
    const notice = () => panel()!.querySelector('[data-end-notice]')!;
    expect(notice().getAttribute('role')).toBe('alert');

    await act(async () => button('대화 목록')!.click());
    await settle();
    await act(async () => button('대화 목록')!.click());
    await settle();
    expect(endNotices()).toEqual(['답이 늦어지고 있으니 다시 보내 주세요.다시 보내기']);
    expect(notice().hasAttribute('role')).toBe(false);
  });

  it('그 뒤에 다른 말이 이어졌으면 앞 실패 안내에는 [다시 보내기]를 두지 않는다', async () => {
    await inConversation(
      [userMsg('m1', '3장 시선 어땠어?', 'r1'), userMsg('m2', '3장 시선 어땠어?', 'r2')],
      'r1',
    );
    await act(async () => transport().emit(ended('r1', 'FAILED', 'AGENT_TIMEOUT')));
    await settle();
    expect(endNotices()).toEqual(['답이 늦어지고 있으니 다시 보내 주세요.']);
    expect(button('다시 보내기')).toBeUndefined();
  });

  it('승인 대기에서 만료로 끝났으면 BE 고정 문장만 — 실행 만료 안내를 함께 붙이지 않는다', async () => {
    await inConversation(
      [
        userMsg('m1', '목표 잡아 줘', 'r1'),
        answer('m2', 'r1', 'PARTIAL', [
          '먼저 판단을 보여 드릴게요.',
          '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.',
        ]),
      ],
      'r1',
    );
    await act(async () => transport().emit(ended('r1', 'EXPIRED', 'AGENT_EXPIRED', true)));
    await settle();
    expect(endNotices()).toEqual([]);
    // PARTIAL 이어도 고정 문장으로 끝났으니 "여기까지 확인했어요" 도 붙이지 않습니다
    expect(panel()!.querySelector('[data-partial]')).toBeNull();
  });

  it('실행 오류가 꺼짐(AGENT_DISABLED)이면 버튼을 숨긴다', async () => {
    await inConversation([], 'r1');
    await act(async () => transport().emit(ended('r1', 'FAILED', 'AGENT_DISABLED')));
    await settle();
    expect(launcher()).toBeNull();
  });

  it('중간까지만 답했으면 "여기까지 확인했어요. 남은 일" 과, 마지막 답에만 [이어서 확인]', async () => {
    const plan = [
      { step: '기록 비교', status: 'DONE', note: null },
      { step: '자료 찾기', status: 'TODO', note: null },
    ];
    await inConversation([
      userMsg('m1', '앞 질문', 'r0'),
      answer('m2', 'r0', 'PARTIAL', ['앞 답'], plan),
      userMsg('m3', '비교해 줘', 'r1'),
      answer('m4', 'r1', 'PARTIAL', ['비교한 데까지'], plan),
    ]);
    const headers = [...panel()!.querySelectorAll('[data-partial]')].map((h) => h.textContent);
    expect(headers).toEqual([
      '여기까지 확인했어요. 남은 일: 자료 찾기',
      '여기까지 확인했어요. 남은 일: 자료 찾기',
    ]);
    const more = [...panel()!.querySelectorAll('button')].filter(
      (b) => visibleText(b) === '이어서 확인',
    );
    expect(more).toHaveLength(1);
    expect(panel()!.querySelector('[data-message="m4"]')!.contains(more[0]!)).toBe(true);
    await act(async () => more[0]!.click());
    expect(transport().sent.map((r) => r.text)).toEqual(['이어서 확인해 줘']);
  });

  it('거절 답이 마지막이면 그 아래에만 할 수 있는 일 · 질문 예시를 붙이고, 예시를 누르면 그 질문을 보낸다', async () => {
    await inConversation([
      userMsg('m1', '날씨 알려 줘', 'r0'),
      answer('m2', 'r0', 'REFUSED', ['자료에서 근거를 찾지 못해 답하지 않을게요.']),
      userMsg('m3', '주식 추천해 줘', 'r1'),
      answer('m4', 'r1', 'REFUSED', [
        '이 요청은 발표 연습 기록으로 도와드릴 수 있는 범위 밖이에요.',
      ]),
    ]);
    const helps = [...panel()!.querySelectorAll('[data-ask-help]')];
    expect(helps).toHaveLength(1);
    // 마지막 거절 답(m4) 뒤에 붙고, 앞의 거절 답(m2) 뒤에는 없습니다
    const m4 = panel()!.querySelector('[data-message="m4"]')!;
    expect(m4.compareDocumentPosition(helps[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(helps[0]!.textContent).toContain(ASK_HELP_TITLE);
    for (const ability of ASK_HELP_ABILITIES) expect(helps[0]!.textContent).toContain(ability);
    const examples = [...helps[0]!.querySelectorAll('button')].map(visibleText);
    expect(examples).toEqual([...ASK_HELP_EXAMPLES]);

    await act(async () => helps[0]!.querySelector('button')!.click());
    expect(transport().sent.map((r) => r.text)).toEqual([ASK_HELP_EXAMPLES[0]]);
  });

  it('거절 답 아래 예시를 눌러 보내면 안내가 바로 사라지고, 초점은 입력칸으로 돌아온다', async () => {
    await inConversation([
      userMsg('m1', '날씨 알려 줘', 'r0'),
      answer('m2', 'r0', 'REFUSED', ['자료에서 근거를 찾지 못해 답하지 않을게요.']),
    ]);
    const example = panel()!.querySelector<HTMLButtonElement>('[data-ask-help] button')!;
    example.focus();
    await act(async () => example.click());
    await settle();
    expect(transport().sent.map((r) => r.text)).toEqual([ASK_HELP_EXAMPLES[0]]);
    // 목록을 다시 읽기 전에도 거절 답(m2)이 마지막이지만, 보내는 중이라 안내를 그리지 않습니다
    expect(panel()!.querySelector('[data-ask-help]')).toBeNull();
    expect(document.activeElement).toBe(input());
  });

  it('보낼 수 없는 동안 예시는 aria-disabled 로 막히고, 숨은 이유를 달고, 눌러도 보내지 않는다', async () => {
    await inConversation([
      userMsg('m1', '날씨 알려 줘', 'r0'),
      answer('m2', 'r0', 'REFUSED', ['자료에서 근거를 찾지 못해 답하지 않을게요.']),
    ]);
    // 429 Retry-After 로 잠깁니다
    await act(async () => {
      const s = useChatStore.getState();
      const ticket = s.beginSend({
        text: '질문',
        page: { route: '/', pitch_id: null, take_id: null, screen: null },
        again_of_message_id: null,
        choice: null,
      })!;
      useChatStore.getState().failSend(ticket, 60);
    });
    const examples = [...panel()!.querySelectorAll<HTMLButtonElement>('[data-ask-help] button')];
    expect(examples).toHaveLength(ASK_HELP_EXAMPLES.length);
    for (const example of examples) {
      expect(example.getAttribute('aria-disabled')).toBe('true');
      expect(example.querySelector('.sr-only')?.textContent).toBeTruthy();
    }
    await act(async () => examples[0]!.click());
    expect(transport().sent).toEqual([]);
  });

  it('거절이 아닌 답이 마지막이면 질문 예시를 붙이지 않는다', async () => {
    await inConversation([
      userMsg('m1', '날씨 알려 줘', 'r0'),
      answer('m2', 'r0', 'REFUSED', ['자료에서 근거를 찾지 못해 답하지 않을게요.']),
      userMsg('m3', '비교해 줘', 'r1'),
      answer('m4', 'r1', 'DONE', ['비교했어요.']),
    ]);
    expect(panel()!.querySelector('[data-ask-help]')).toBeNull();
  });

  it('빈 대화에서는 첫 인사 아래에 같은 안내를 보이고, 예시를 누르면 그 질문을 보낸다', async () => {
    await inConversation([]);
    expect(panel()!.querySelector('[data-chat-empty]')!.textContent).toContain(
      '새미나이에게 물어보세요',
    );
    const help = panel()!.querySelector('[data-ask-help]')!;
    expect(help.textContent).toContain(ASK_HELP_TITLE);
    expect([...help.querySelectorAll('button')].map(visibleText)).toEqual([...ASK_HELP_EXAMPLES]);

    await act(async () => help.querySelectorAll('button')[1]!.click());
    expect(transport().sent.map((r) => r.text)).toEqual([ASK_HELP_EXAMPLES[1]]);
  });

  it.each([
    ['AGENT_DAILY_LIMIT', '오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요.'],
    ['AGENT_CONTEXT_TOO_LARGE', '자료가 너무 많으니 범위를 좁혀 다시 물어봐 주세요.'],
    ['VALIDATION_ERROR', '메시지를 확인해 주세요.'],
  ])('보내기 거절 %s → 코드별 문구', async (code, text) => {
    await visit('/');
    await openPanel();
    await act(async () => transport().emit({ type: 'sendFailed', code, status: 400 }));
    expect(alerts()).toContain(text);
  });

  it('다른 탭의 실행이 있어 받지 못하면 "이 대화의 답변을 만들고 있어요."', async () => {
    sendMode.value = 'conflict';
    await inConversation([]);
    await type('질문');
    await press(input(), { key: 'Enter' });
    await settle();
    expect(alerts()).toContain('이 대화의 답변을 만들고 있어요.');
    // 받지 못했으니 입력을 되돌립니다
    expect(input().value).toBe('질문');
  });

  it('승인 · 거절이 받아들여지지 않으면 코드별 문구', async () => {
    await visit('/');
    await openPanel();
    await act(async () =>
      transport().emit({
        type: 'decisionFailed',
        runId: 'r1',
        actionId: 'a-1',
        code: 'APPROVAL_HASH_MISMATCH',
      }),
    );
    expect(alerts()).toContain('요청이 변경되어 다시 확인이 필요해요.');
  });

  it('기다리라는 시각이 지나면 보내기 잠금을 저절로 푼다 (429 Retry-After)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await visit('/');
    await openPanel();
    await type('질문');
    await act(async () => {
      const s = useChatStore.getState();
      const ticket = s.beginSend({
        text: '질문',
        page: { route: '/', pitch_id: null, take_id: null, screen: null },
        again_of_message_id: null,
        choice: null,
      })!;
      useChatStore.getState().failSend(ticket, 5);
    });
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    await wait(4_000);
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    await wait(1_100);
    expect(sendButton().getAttribute('aria-disabled')).toBe('false');
  });

  it('조회 실패 · 늦어짐은 [상태 확인]과 함께, 진행을 받거나 끝나면 사라진다', async () => {
    await inConversation([], 'r1');
    const status = () => panel()!.querySelector('[data-run-notice]')?.textContent ?? null;
    await act(async () => transport().emit({ type: 'pollTrouble', failures: 2, reason: 'FAILED' }));
    expect(status()).toBe('진행 상태를 확인하지 못해 다시 연결하고 있어요.상태 확인');
    await act(async () => button('상태 확인')!.click());
    expect(transport().checks).toBe(1);
    expect(status()).toBeNull();

    await act(async () => transport().emit({ type: 'slow', runId: 'r1' }));
    expect(status()).toBe('결과 확인이 늦어지고 있으니 상태를 다시 확인해 주세요.상태 확인');
    await act(async () =>
      transport().emit({
        type: 'progress',
        runId: 'r1',
        conversationId: 'c1',
        status: 'RUNNING',
        events: [],
        claims: [],
        restart: true,
        catchUp: true,
        contractError: null,
        plan: null,
        trace: null,
        progressDropped: false,
        cards: [],
      }),
    );
    expect(status()).toBeNull();

    await act(async () =>
      transport().emit({ type: 'pollTrouble', failures: 1, reason: 'TOO_LARGE' }),
    );
    // 크기 초과는 다시 확인해도 같아 버튼을 두지 않습니다
    expect(status()).toBe('일부 진행 정보를 표시하지 못했어요.');
    await act(async () => transport().emit({ type: 'pollRecovered' }));
    expect(status()).toBeNull();
  });

  it('취소 확인 중 안내는 진행을 받아도 남고, 끝나면 사라진다', async () => {
    await inConversation([], 'r1');
    const status = () => panel()!.querySelector('[data-run-notice]')?.textContent ?? null;
    await act(async () => transport().emit({ type: 'cancelChecking', runId: 'r1' }));
    expect(status()).toBe('취소 여부를 확인하고 있어요.');
    await act(async () =>
      transport().emit({
        type: 'progress',
        runId: 'r1',
        conversationId: 'c1',
        status: 'RUNNING',
        events: [],
        claims: [],
        restart: true,
        catchUp: true,
        contractError: null,
        plan: null,
        trace: null,
        progressDropped: false,
        cards: [],
      }),
    );
    expect(status()).toBe('취소 여부를 확인하고 있어요.');
    await act(async () => transport().emit(ended('r1', 'CANCELLED', 'AGENT_CANCELLED')));
    expect(status()).toBeNull();
    expect(endNotices()).toEqual(['요청을 취소했어요.']);
  });

  describe('자동 스크롤', () => {
    /** jsdom 은 배치를 계산하지 않아 높이를 정해 둡니다 */
    function fakeBox(el: HTMLElement, height: () => number) {
      Object.defineProperty(el, 'scrollHeight', { configurable: true, get: height });
      Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 100 });
    }
    const box = () => panel()!.querySelector<HTMLElement>('[aria-label="채팅 메시지"]')!;
    const grow = (text: string) =>
      act(async () =>
        transport().emit({
          type: 'progress',
          runId: 'r1',
          conversationId: 'c1',
          status: 'RUNNING',
          events: [],
          claims: [{ ...claim(text), index: 0 }],
          restart: true,
          catchUp: true,
          contractError: null,
          plan: null,
          trace: null,
          progressDropped: false,
          cards: [],
        }),
      );

    it('맨 아래를 보고 있으면 새 글을 따라 내려가고, 위로 올려 읽는 중에는 끌어내리지 않는다', async () => {
      await inConversation([], 'r1');
      let height = 500;
      fakeBox(box(), () => height);
      await grow('첫 문장');
      expect(box().scrollTop).toBe(500);

      // 위로 올려 읽음
      box().scrollTop = 100;
      await act(async () => box().dispatchEvent(new Event('scroll')));
      height = 800;
      await grow('다음 문장');
      expect(box().scrollTop).toBe(100);

      // 다시 맨 아래로 오면 따라갑니다
      box().scrollTop = 700;
      await act(async () => box().dispatchEvent(new Event('scroll')));
      height = 900;
      await grow('세 번째 문장');
      expect(box().scrollTop).toBe(900);
    });

    it('위로 올려 읽는 중이어도 내가 보내면 맨 아래로 간다', async () => {
      await inConversation([]);
      let height = 500;
      fakeBox(box(), () => height);
      box().scrollTop = 0;
      await act(async () => box().dispatchEvent(new Event('scroll')));
      height = 600;
      await type('질문');
      await press(input(), { key: 'Enter' });
      expect(box().scrollTop).toBe(600);
    });
  });
});

// ── 8-6 근거 배지 · 최신 아님 · 다시 묻기 ───────────────────────

describe('근거 배지 · 최신 아님 (12-4 F5)', () => {
  const BASIS_PITCH = '40000000-0000-4000-8000-000000000004';
  const answerWith = (over: Record<string, unknown> = {}) => ({
    message_id: 'm2',
    role: 'assistant',
    status: 'DONE',
    text: '3장 시선이 낮았어요.',
    run_id: 'r1',
    claims: [
      {
        kind: 'GENERAL',
        text: '3장 시선이 낮았어요.',
        evidence_ids: [],
        source_ids: [],
        assessment_id: null,
        action_id: null,
      },
    ],
    cards: [],
    plan: null,
    trace: null,
    masked: false,
    basis: {
      pitches: [
        {
          pitch_id: BASIS_PITCH,
          script_version_id: 'sv-2',
          presentation_version_id: 'pv-2',
          standard_version: 4,
          takes: [{ take_id: 't-5', take_number: 5 }],
        },
      ],
    },
    stale_reasons: ['NEW_TAKE'],
    ...over,
  });
  const question = { message_id: 'm1', role: 'user', text: '3장 어땠어?', run_id: 'r1' };

  async function inConversation(messages: unknown[]) {
    server.messages = () => Promise.resolve({ messages, next_before_seq: null });
    server.resources = () =>
      Promise.resolve({
        pitch_id: BASIS_PITCH,
        presentation_versions: [],
        script_versions: [{ id: 'sv-2', version: 2 }],
        evaluation_versions: [],
      });
    await visit('/');
    await openPanel();
    const st = useChatStore.getState();
    st.selectConversation('c1', { pitchId: null, title: null });
    st.resolveConversation(chatTicket(), null);
    await settle();
    // 배지의 버전 목록은 메시지를 그린 뒤에 읽습니다
    await settle();
  }

  it('답마다 "Take 5 · 대본 V2 · 기준 v4 기준", 최신이 아니면 사유를 붙이고 본문을 흐린다', async () => {
    await inConversation([question, answerWith()]);
    const bubble = panel()!.querySelector('[data-message="m2"]')!;
    expect(bubble.querySelector('[data-basis]')!.textContent).toBe(
      'Take 5 · 대본 V2 · 기준 v4 기준',
    );
    expect(bubble.querySelector('[data-stale-reason]')!.textContent).toBe(
      '최신 아님 · 이 답 뒤에 새 Take가 있어요',
    );
    expect(bubble.querySelector('.opacity-60')!.textContent).toContain('3장 시선이 낮았어요.');
  });

  it('최신이면 사유 · 흐림 · 다시 묻기가 없다', async () => {
    await inConversation([question, answerWith({ stale_reasons: [] })]);
    const bubble = panel()!.querySelector('[data-message="m2"]')!;
    expect(bubble.querySelector('[data-basis]')).not.toBeNull();
    expect(bubble.querySelector('[data-stale-reason]')).toBeNull();
    expect(bubble.querySelector('.opacity-60')).toBeNull();
    expect(button('최신 기준으로 다시 묻기')).toBeUndefined();
  });

  it('[최신 기준으로 다시 묻기]는 같은 질문을 again_of_message_id(그 답)와 함께 새로 보내고, 옛 답은 그대로 둔다', async () => {
    await inConversation([question, answerWith()]);
    await act(async () => button('최신 기준으로 다시 묻기')!.click());
    expect(transport().sent).toHaveLength(1);
    expect(transport().sent[0]).toMatchObject({
      text: '3장 어땠어?',
      again_of_message_id: 'm2',
      conversation_id: 'c1',
    });
    expect(panel()!.querySelector('[data-message="m2"]')).not.toBeNull();
  });

  it('질문을 모르면(앞 페이지 등) 다시 묻기 버튼을 두지 않는다', async () => {
    await inConversation([answerWith()]);
    expect(panel()!.querySelector('[data-stale-reason]')).not.toBeNull();
    expect(button('최신 기준으로 다시 묻기')).toBeUndefined();
  });

  it('실행 중에는 다시 묻기를 잠근다', async () => {
    await inConversation([question, answerWith()]);
    await act(async () => {
      useChatStore.getState().selectConversation('c1', { pitchId: null, title: null });
      useChatStore.getState().resolveConversation(chatTicket(), { runId: 'r9', phase: 'RUNNING' });
    });
    await settle();
    const again = button('최신 기준으로 다시 묻기')!;
    expect(again.getAttribute('aria-disabled')).toBe('true');
    await act(async () => again.click());
    expect(transport().sent).toEqual([]);
  });

  it('근거의 피치 ID 가 UUID 가 아니면 버전 목록을 읽지 않고, 지워진 피치(404)는 다시 묻지 않는다', async () => {
    const reads = () => server.seen.filter((x) => x.path.endsWith('/resources')).length;
    await inConversation([
      question,
      answerWith({
        basis: {
          pitches: [
            {
              pitch_id: '../../x',
              script_version_id: 'sv-2',
              standard_version: 4,
              takes: [{ take_id: 't-5', take_number: 5 }],
            },
          ],
        },
      }),
    ]);
    expect(reads()).toBe(0);
  });

  it('지워진 피치(404)의 버전 목록은 다시 묻지 않는다', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const reads = () => server.seen.filter((x) => x.path.endsWith('/resources')).length;
    server.messages = () =>
      Promise.resolve({ messages: [question, answerWith()], next_before_seq: null });
    server.resources = () => Promise.reject(new ApiFailure(404, 'PITCH_NOT_FOUND'));
    await visit('/');
    await openPanel();
    const st = useChatStore.getState();
    st.selectConversation('c1', { pitchId: null, title: null });
    st.resolveConversation(chatTicket(), null);
    await settle();
    await wait(5_000);
    expect(reads()).toBe(1);
    expect(panel()!.querySelector('[data-basis]')!.textContent).toBe('Take 5 · 기준 v4 기준');
  });

  it('버전 목록을 읽지 못하면 대본 칸만 빼고 보인다', async () => {
    await inConversation([question, answerWith()]);
    server.resources = () => Promise.reject(new Error('끊김'));
    queryClient.removeQueries({ queryKey: ['pitches', BASIS_PITCH, 'resources'] });
    await act(async () => useChatStore.getState().startNewConversation(null));
    await act(async () => {
      useChatStore.getState().selectConversation('c1', { pitchId: null, title: null });
      useChatStore.getState().resolveConversation(chatTicket(), null);
    });
    await settle();
    expect(panel()!.querySelector('[data-basis]')!.textContent).toBe('Take 5 · 기준 v4 기준');
  });
});

// ── 두 번째 점검 반영 ─────────────────────────────────────

describe('두 번째 점검 반영', () => {
  const answerMsg = (id: string, runId: string, status: string, stale: string[] = []) => ({
    message_id: id,
    role: 'assistant',
    status,
    text: '답.',
    run_id: runId,
    claims: [
      {
        kind: 'GENERAL',
        text: '답.',
        evidence_ids: [],
        source_ids: [],
        assessment_id: null,
        action_id: null,
      },
    ],
    cards: [],
    plan: null,
    trace: null,
    masked: false,
    basis: null,
    stale_reasons: stale,
  });
  const q = { message_id: 'm1', role: 'user', text: '질문', run_id: 'r1' };

  async function conv(messages: unknown[], runId: string | null = null) {
    server.messages = () => Promise.resolve({ messages, next_before_seq: null });
    await visit('/');
    await openPanel();
    const st = useChatStore.getState();
    st.selectConversation('c1', { pitchId: null, title: null });
    st.resolveConversation(chatTicket(), runId ? { runId, phase: 'RUNNING' } : null);
    await settle();
  }

  it('범위를 다시 고를 때까지는 카드 · 답 아래 버튼으로도 보내지 않는다 (L3-07)', async () => {
    await conv([q, answerMsg('m2', 'r1', 'PARTIAL')]);
    // 범위가 피치인 대화에서 보내기가 404 — 범위를 다시 고를 때까지 막힙니다
    await act(async () => {
      useChatStore.setState({ scope: { pitchId: 'p-1', title: '발표' } });
    });
    await act(async () => transport().emit({ type: 'sendFailed', code: 'NOT_FOUND', status: 404 }));
    const more = button('이어서 확인')!;
    expect(more.getAttribute('aria-disabled')).toBe('true');
    expect(more.querySelector('.sr-only')!.textContent).toContain(
      '범위를 다시 고른 뒤 누를 수 있어요',
    );
    await act(async () => more.click());
    expect(transport().sent).toEqual([]);
  });

  it('위로 올려 읽는 중이어도 [최신 기준으로 다시 묻기]로 보내면 맨 아래로 간다', async () => {
    await conv([q, answerMsg('m2', 'r1', 'DONE', ['NEW_TAKE'])]);
    const box = panel()!.querySelector<HTMLElement>('[aria-label="채팅 메시지"]')!;
    let height = 500;
    Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => height });
    Object.defineProperty(box, 'clientHeight', { configurable: true, get: () => 100 });
    box.scrollTop = 0;
    await act(async () => box.dispatchEvent(new Event('scroll')));
    height = 700;
    await act(async () => button('최신 기준으로 다시 묻기')!.click());
    expect(box.scrollTop).toBe(700);
  });

  it('다른 탭이 로그아웃했다가 같은 계정으로 다시 로그인하면, 세션을 다시 확인한 뒤 다시 보인다', async () => {
    state.session = { ...state.session, dataUpdatedAt: 1 } as typeof state.session;
    await visit('/');
    expect(launcher()).not.toBeNull();
    await act(async () => useChatStore.getState().signOut());
    await go('/pitches');
    // 세션 값은 그 전에 확인한 것 — 바로 다시 보이지 않습니다
    expect(launcher()).toBeNull();
    // 세션을 다시 확인했더니 같은 계정
    state.session = { ...state.session, dataUpdatedAt: 2 } as typeof state.session;
    await go('/');
    expect(launcher()).not.toBeNull();
    expect(useChatStore.getState().userId).toBe('u1');
  });

  it('서버가 접수하지 못했다고 알리면 "요청을 접수하지 못했으니 다시 보내 주세요." 와 [다시 보내기](같은 키)', async () => {
    await visit('/');
    await openPanel();
    await act(async () =>
      transport().emit({ type: 'unconfirmed', sendId: 's1', notAccepted: true }),
    );
    expect(alerts()).toContain('요청을 접수하지 못했으니 다시 보내 주세요.다시 보내기');
    await act(async () => button('다시 보내기')!.click());
    expect(transport().retried).toBe(1);
  });

  it('잠긴 버튼 · 칩은 왜 잠겼는지 화면 읽기 프로그램에 알린다', async () => {
    await conv([q, answerMsg('m2', 'r1', 'PARTIAL')], 'r9');
    expect(button('이어서 확인')!.querySelector('.sr-only')!.textContent).toContain(
      '답을 만드는 중이에요',
    );
    // 칩은 aria-label 없이 글자로 이름을 두어 잠긴 이유까지 이름에 들어갑니다
    const chip = panel()!.querySelector('[data-chat-scope]')!;
    expect(chip.hasAttribute('aria-label')).toBe(false);
    expect(chip.textContent).toContain('답을 만드는 동안에는 범위를 바꿀 수 없어요');
    // [입력]은 글을 썼을 때만 이유를 붙입니다
    expect(sendButton().querySelectorAll('.sr-only')).toHaveLength(1);
    await type('질문');
    expect(sendButton().textContent).toBe('입력 (답을 만드는 중이에요)');
    await act(async () => useChatStore.getState().endRun('r9'));
    expect(button('이어서 확인')!.querySelector('.sr-only')).toBeNull();
    expect(chip.textContent).not.toContain('바꿀 수 없어요');
    expect(sendButton().querySelectorAll('.sr-only')).toHaveLength(1);
  });
});

// ── 10단계 기억 설정 · '기억 없이' ─────────────────────────────
describe("기억 설정 · '기억 없이' (12-4 F14 · F15)", () => {
  const INTRO = '코칭 프로필 기억 — 말투';
  const intro = () => panel()!.querySelector('[data-memory-intro]');
  const memorySwitch = () =>
    panel()!.querySelector<HTMLInputElement>('[data-memory-off-switch] input');
  const badge = () => panel()!.querySelector('[data-memory-off]');

  it('첫 안내는 처음 연 때 한 번만 보이고, 다시 열면 보이지 않는다 — 사용자마다 따로 (L3-43)', async () => {
    await visit('/');
    await openPanel();
    expect(intro()!.textContent).toContain(INTRO);
    expect(intro()!.textContent).toContain(
      "'기억 없이' 대화를 골라도 그 대화의 기록 · 요약은 보관되고",
    );

    await act(async () => button('새미나이 닫기')!.click());
    await openPanel();
    expect(intro()).toBeNull();

    // 다른 사용자는 처음입니다
    await act(async () => root.unmount());
    root = createRoot(host);
    state.session = { ...state.session, data: { id: 'u2' } };
    try {
      await visit('/');
      await openPanel();
      expect(intro()).not.toBeNull();
    } finally {
      state.session = { ...state.session, data: { id: 'u1' } };
    }
  });

  it('[기억 설정]은 대화창 안에서 설정 화면으로 바꾸고 입력칸을 숨긴다 — [돌아가기]로 대화로 돌아온다', async () => {
    server.memory = () =>
      Promise.resolve({ enabled: true, items: [{ item_id: 'i-1', key: 'TONE', value: '짧게' }] });
    await visit('/');
    await openPanel();
    await type('쓰던 글');
    await act(async () => button('기억 설정')!.click());
    await settle();
    expect(button('기억 설정')!.getAttribute('aria-pressed')).toBe('true');
    const settings = panel()!.querySelector('[data-memory-settings]')!;
    expect(settings.textContent).toContain(INTRO);
    expect(settings.textContent).toContain('답 말투 · 길이');
    expect(settings.textContent).toContain('짧게');
    expect(panel()!.querySelector('form')!.hidden).toBe(true);

    await act(async () => button('돌아가기')!.click());
    expect(panel()!.querySelector('[data-memory-settings]')).toBeNull();
    expect(panel()!.querySelector('form')!.hidden).toBe(false);
    expect(input().value).toBe('쓰던 글');
  });

  it('[돌아가기]는 초점을 [기억 설정]으로 돌리고, 설정 화면에서 누른 줄이 사라지면 초점을 패널에 둬 Esc 로 닫힌다', async () => {
    server.memory = () =>
      Promise.resolve({ enabled: true, items: [{ item_id: 'i-1', key: 'TONE', value: '짧게' }] });
    await visit('/');
    await openPanel();
    await act(async () => button('기억 설정')!.click());
    await settle();
    button('돌아가기')!.focus();
    await act(async () => button('돌아가기')!.click());
    await settle();
    expect(document.activeElement).toBe(button('기억 설정'));

    await act(async () => button('기억 설정')!.click());
    await settle();
    const remove = panel()!.querySelector<HTMLButtonElement>('[data-memory-row] button')!;
    remove.focus();
    await act(async () => remove.click());
    await settle();
    expect(panel()!.querySelector('[data-memory-row]')).toBeNull();
    expect(document.activeElement).toBe(panel());
    await press(document.activeElement!, { key: 'Escape' });
    expect(useChatStore.getState().open).toBe(false);
  });

  it("'기억 없이'는 새 대화에서 보내기 전에만 고르고, 고르면 머리에 표시한다 — 대화가 생기면 스위치가 사라진다 (L3-44)", async () => {
    await visit('/');
    await openPanel();
    await settle();
    expect(memorySwitch()!.checked).toBe(false);
    expect(badge()).toBeNull();

    await act(async () => memorySwitch()!.click());
    expect(useChatStore.getState().memoryOff).toBe(true);
    expect(memorySwitch()!.checked).toBe(true);
    expect(badge()).toBeNull(); // 설정 가능한 동안에는 스위치 자체가 상태를 표시합니다.

    // 대화가 생긴 뒤에는 바꿀 수 없습니다 — 스위치를 거두고 표시는 남깁니다
    await act(async () => {
      useChatStore.setState({ conversationId: 'c-new' });
    });
    expect(memorySwitch()).toBeNull();
    expect(useChatStore.getState().setMemoryOff(false)).toBe(false);
    expect(badge()!.textContent).toBe('기억 없이');
  });

  it("코칭 프로필 기억이 꺼져 있으면 '기억 없이' 스위치는 꺼진 채 막힌다 (L3-44 설정 꺼짐)", async () => {
    server.memory = () => Promise.resolve({ enabled: false, items: [] });
    await visit('/');
    useChatStore.getState().setMemoryOff(true);
    await openPanel();
    await settle();
    expect(memorySwitch()!.checked).toBe(false);
    expect(memorySwitch()!.disabled).toBe(true);
    expect(panel()!.querySelector('[data-memory-off-switch]')!.textContent).toContain(
      '기억 설정이 꺼져 있어요',
    );
    // 보이는 것과 보낼 값이 같게 고른 것을 풉니다
    expect(useChatStore.getState().memoryOff).toBe(false);
  });

  it("대화 목록의 '기억 없이' 대화는 줄에 표시하고, 고르면 그 값을 저장소에 넘겨 머리에도 표시한다", async () => {
    server.conversations = () =>
      Promise.resolve({
        conversations: [
          {
            conversation_id: 'c8',
            title: '기억 없이 물어본 것',
            scope_pitch_id: null,
            scope_pitch_title: null,
            last_user_activity_at: '2026-11-05T12:30:00Z',
            created_at: '2026-11-05T12:00:00Z',
            stale: false,
            memory_off: true,
          },
          {
            conversation_id: 'c9',
            title: '보통 대화',
            scope_pitch_id: null,
            scope_pitch_title: null,
            last_user_activity_at: '2026-11-05T12:30:00Z',
            created_at: '2026-11-05T12:00:00Z',
            stale: false,
            memory_off: false,
          },
        ],
      });
    await visit('/');
    await openPanel();
    await act(async () => button('대화 목록')!.click());
    await settle();
    const rows = () => [
      ...panel()!.querySelectorAll<HTMLButtonElement>('[aria-label="지난 대화"] button'),
    ];
    expect(rows()[0]!.textContent).toContain('기억 없이');
    expect(rows()[1]!.textContent).not.toContain('기억 없이');

    await act(async () => rows()[0]!.click());
    await settle();
    expect(useChatStore.getState().memoryOff).toBe(true);
    expect(badge()!.textContent).toBe('기억 없이');

    await act(async () => button('대화 목록')!.click());
    await settle();
    await act(async () => rows()[1]!.click());
    await settle();
    expect(useChatStore.getState().memoryOff).toBe(false);
    expect(badge()).toBeNull();
  });
});
