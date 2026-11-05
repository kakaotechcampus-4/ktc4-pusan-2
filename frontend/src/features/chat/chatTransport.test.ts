// @vitest-environment jsdom
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => 'u' }));
import { createChatHandlers, type MockChatControls } from '@/mocks/chat';
import { retryAfterSec } from '@/shared/api/client';
import { chatPaths } from '@/shared/api/chatPaths';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { RunView } from '@/types/chat';
import type { ChatApi } from './chatApi';
import { canSend, useChatStore } from './chatStore';
import {
  createChatTransport,
  type ChatClock,
  type ChatTransport,
  type TransportSignal,
} from './chatTransport';

// ── 시험 도구 ───────────────────────────────────────────────

/** 시험이 돌리는 시계 — advance 로 시간을 보내면 그 사이의 타이머를 차례로 부릅니다 */
class ManualClock implements ChatClock {
  t = 0;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  now = () => this.t;
  random = () => 0.5;
  setTimeout = (fn: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };
  clearTimeout = (handle: unknown) => {
    this.timers.delete(handle as number);
  };
  pending = () => [...this.timers.values()].map((x) => x.at - this.t).sort((a, b) => a - b);
  async advance(ms: number) {
    const end = this.t + ms;
    for (;;) {
      const next = [...this.timers.entries()]
        .filter(([, x]) => x.at <= end)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      this.t = next[1].at;
      this.timers.delete(next[0]);
      next[1].fn();
      await flush();
    }
    this.t = end;
    await flush();
  }
}

/** 진행 중인 요청 수 — 시험용 API 가 셉니다 */
let inflight = 0;
/**
 * 한 바퀴 — setTimeout(0) 은 jsdom 에서 느려 파일 하나에 1분 가까이 걸렸습니다. Node 의 setImmediate 를 씁니다
 * (앱 tsconfig 에는 Node 타입이 없어 globalThis 로 부릅니다)
 */
const nextTurn = (globalThis as unknown as { setImmediate: (fn: () => void) => void }).setImmediate;
const tick = () => new Promise<void>((resolve) => nextTurn(resolve));

/** 진행 중인 요청이 다 끝나고 약속이 풀릴 때까지 기다림 — 끝나지 않게 붙잡은 요청이 있으면 0.3초까지만 */
const flush = async () => {
  const deadline = Date.now() + 300;
  do for (let i = 0; i < 5; i++) await tick();
  while (inflight > 0 && Date.now() < deadline);
  for (let i = 0; i < 5; i++) await tick();
};

/** 요청 하나를 세며 부름 */
const counted =
  <A extends unknown[], R>(fn: (...args: A) => Promise<R>) =>
  async (...args: A) => {
    inflight += 1;
    try {
      return await fn(...args);
    } finally {
      inflight -= 1;
    }
  };

const base = 'http://localhost';
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { code?: string } | null;
    throw new ApiFailure(
      response.status,
      body?.code ?? 'REQUEST_FAILED',
      retryAfterSec(response.headers.get('Retry-After')),
    );
  }
  return response.json() as Promise<T>;
}
const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** 목 BE 를 부르는 API — 실제 chatApi 와 같은 경로 · 모양 */
const mockApi: ChatApi = {
  send: (request) => call(chatPaths.send, json('POST', request)),
  run: (runId, afterSeq) => call(`${chatPaths.run(runId)}?after_seq=${afterSeq}`),
  conversationRun: (id) => call(chatPaths.conversationRun(id)),
  cancel: (runId) => call(chatPaths.cancel(runId), json('POST', {})),
  createConversation: (request) => call(chatPaths.conversations, json('POST', request)),
  changeScope: (id, pitchId) => call(chatPaths.scope(id), json('PUT', { pitch_id: pitchId })),
};

const draft = (text = '비교해 줘') => ({
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  again_of_message_id: null,
  choice: null,
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

const store = () => useChatStore.getState();
let controls: MockChatControls;
let clock: ManualClock;
let transport: ChatTransport;
let signals: TransportSignal[];
let api: ChatApi;
let runCalls: number[];
let online: () => void;

function start(override: Partial<ChatApi> = {}) {
  api = { ...mockApi, ...override };
  const tracked: ChatApi = {
    send: counted(api.send),
    run: counted((runId: string, afterSeq: number) => {
      runCalls.push(clock.t);
      return api.run(runId, afterSeq);
    }),
    conversationRun: counted(api.conversationRun),
    cancel: counted(api.cancel),
    createConversation: counted(api.createConversation),
    changeScope: counted(api.changeScope),
  };
  transport = createChatTransport({
    api: tracked,
    clock,
    onOnline: (listener) => {
      online = listener;
      return () => undefined;
    },
  });
  transport.subscribe((signal) => signals.push(signal));
}

const of = <T extends TransportSignal['type']>(type: T) =>
  signals.filter((s): s is Extract<TransportSignal, { type: T }> => s.type === type);

/** 보내고, 이어진 첫 조회 응답까지 처리되게 기다림 — 다음 조회는 응답을 받은 때부터 셉니다 */
async function sendNow(d: ReturnType<typeof draft>) {
  const result = await transport.send(d);
  await flush();
  return result;
}

/** 끝날 때까지 시간을 보냄 */
async function untilEnded(limitMs = 60_000) {
  for (let spent = 0; spent < limitMs && store().run; spent += 1_000) await clock.advance(1_000);
}

beforeEach(() => {
  inflight = 0;
  sessionStorage.clear();
  const chat = createChatHandlers(() => 'u');
  controls = chat.controls;
  server.resetHandlers(...chat.handlers);
  clock = new ManualClock();
  signals = [];
  runCalls = [];
  store().signOut();
  store().setUser('u');
  store().openChat({ pitchId: null, title: null });
  start();
});

afterEach(() => transport.dispose());

// ── 보내기 → 조회 ───────────────────────────────────────────

describe('보내기 → 조회', () => {
  it('보내면 접수 뒤 조회해 문장을 순서대로 한 번씩 받고, 끝나면 알린다', async () => {
    expect(await sendNow(draft())).toBe('accepted');
    await untilEnded();
    const claims = of('progress')
      .flatMap((s) => s.events)
      .flatMap((e) => (e.event === 'claim' ? [e.data.index] : []));
    expect(claims).toEqual([0, 1, 2]);
    const seqs = of('progress').flatMap((s) => s.events.map((e) => e.event_seq));
    expect(seqs).toEqual(seqs.map((_, i) => i + 1));
    expect(of('ended')).toMatchObject([{ status: 'DONE', errorCode: null }]);
    expect([store().run, canSend(store())]).toEqual([null, true]);
  });

  it('대기 중에는 2초, 실행 중에는 1초 간격으로 조회한다', async () => {
    await sendNow(draft());
    await untilEnded();
    const gaps = runCalls.slice(1).map((t, i) => t - runCalls[i]!);
    // 첫 조회(대기) 뒤 2초, 그 뒤 실행 중은 1초
    expect(gaps[0]).toBe(2_000);
    expect(gaps.slice(1).every((g) => g === 1_000)).toBe(true);
  });

  it('조회는 한 번에 하나만 — 응답을 기다리는 동안 다시 불러도 겹치지 않는다', async () => {
    let release!: () => void;
    let inFlight = 0;
    let maxInFlight = 0;
    start({
      run: async (runId, afterSeq) => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise<void>((resolve) => (release = resolve));
        inFlight -= 1;
        return mockApi.run(runId, afterSeq);
      },
    });
    const sending = transport.send(draft());
    await flush();
    transport.checkNow();
    transport.checkNow();
    await flush();
    release();
    await sending;
    await flush();
    expect(maxInFlight).toBe(1);
  });

  it('건너뛴 순번이 있으면 처리하지 않고 같은 커서로 곧바로 다시 읽는다', async () => {
    const plan = (seq: number) => ({
      event_seq: seq,
      event: 'plan' as const,
      data: { plan: [] },
    });
    const view = (status: RunView['status'], seqs: number[]): RunView => ({
      run_id: store().run!.runId,
      conversation_id: store().run!.conversationId,
      status,
      plan: [],
      trace: [],
      events: seqs.map(plan),
      next_after_seq: null,
      cards: [],
      error: null,
      retry_after_s: null,
    });
    const asked: number[] = [];
    // 첫 응답은 1 을 건너뛰고 2 · 3 만, 다시 읽으면 1 · 2 · 3 과 끝
    const replies = [() => view('RUNNING', [2, 3]), () => view('DONE', [1, 2, 3])];
    start({
      run: async (_runId, afterSeq) => {
        asked.push(afterSeq);
        return replies.shift()!();
      },
    });
    await sendNow(draft());
    // 곧바로 다시 읽기는 0ms 뒤로 예약됩니다
    await clock.advance(0);
    expect(asked).toEqual([0, 0]);
    const seqs = of('progress').flatMap((s) => s.events.map((e) => e.event_seq));
    expect(seqs).toEqual([1, 2, 3]);
    expect(of('ended')).toHaveLength(1);
  });
});

// ── 조회 실패 · 늦어짐 ──────────────────────────────────────

describe('조회 실패 · 늦어짐', () => {
  it('실패하면 2 · 4초로 늘려 다시 조회하고, 되면 회복을 알린다 — 형식이 틀린 응답도 실패다', async () => {
    let failing = 2;
    start({
      run: async (runId, afterSeq) => {
        if (failing-- > 0) return { nope: true } as unknown as RunView;
        return mockApi.run(runId, afterSeq);
      },
    });
    await sendNow(draft());
    await clock.advance(10_000);
    expect(runCalls.slice(0, 3)).toEqual([0, 2_000, 6_000]);
    expect(of('pollTrouble').map((s) => s.failures)).toEqual([1, 2]);
    expect(of('pollRecovered')).toHaveLength(1);
  });

  it('연결이 돌아오면 기다리지 않고 바로 조회한다', async () => {
    start({
      run: async () => {
        throw new TypeError('offline');
      },
    });
    await sendNow(draft());
    const before = runCalls.length;
    online();
    await flush();
    expect(runCalls.length).toBe(before + 1);
  });

  it('130초가 지나도 끝나지 않으면 한 번 알리고 30초 간격으로 늦춘다 — 직접 확인하면 다시 센다', async () => {
    start({
      run: async (runId) => ({
        run_id: runId,
        conversation_id: store().run!.conversationId,
        status: 'RUNNING',
        plan: [],
        trace: [],
        events: [],
        next_after_seq: null,
        cards: [],
        error: null,
        retry_after_s: null,
      }),
    });
    await sendNow(draft());
    await clock.advance(131_000);
    expect(of('slow')).toHaveLength(1);
    expect(clock.pending()).toEqual([30_000 - (131_000 - 130_000)]);
    transport.checkNow();
    await flush();
    expect(clock.pending()).toEqual([1_000]);
  });

  it('실행 기록이 없어지면(404) 실행을 놓고 알린다 — 화면은 목록에서 복구한다', async () => {
    start({
      run: async () => {
        throw new ApiFailure(404, 'NOT_FOUND');
      },
    });
    await sendNow(draft());
    await flush();
    expect(of('lost')).toHaveLength(1);
    expect(store().run).toBeNull();
  });

  it('로그아웃한 뒤 도착한 조회 응답은 버린다', async () => {
    let release!: () => void;
    start({
      run: async (runId, afterSeq) => {
        await new Promise<void>((resolve) => (release = resolve));
        return mockApi.run(runId, afterSeq);
      },
    });
    await sendNow(draft());
    await flush();
    store().signOut();
    release();
    await flush();
    expect(of('progress')).toHaveLength(0);
  });
});

// ── 보내기 실패 ─────────────────────────────────────────────

describe('보내기 실패', () => {
  it('15초 안에 접수를 모르면 미확정으로 알리고 자동으로 다시 보내지 않는다 — 다시 확인하면 같은 키로', async () => {
    const bodies: string[] = [];
    let hold = true;
    start({
      send: async (request) => {
        bodies.push(request.client_request_id);
        if (hold) await new Promise(() => undefined);
        return mockApi.send(request);
      },
    });
    transport.send(draft()).catch(() => undefined);
    await flush();
    await clock.advance(15_000);
    expect(of('unconfirmed')).toHaveLength(1);
    expect([store().pending?.unconfirmed, canSend(store())]).toEqual([true, false]);
    await clock.advance(60_000);
    expect(bodies).toHaveLength(1);

    hold = false;
    expect(await transport.retryUnconfirmed()).toBe('accepted');
    expect(bodies).toEqual([bodies[0], bodies[0]]);
  });

  it('연결이 끊겨 실패하면 미확정 — 다시 확인하면 접수된다', async () => {
    let offline = true;
    start({
      send: async (request) => {
        if (offline) throw new TypeError('Failed to fetch');
        return mockApi.send(request);
      },
    });
    expect(await sendNow(draft())).toBe('unconfirmed');
    offline = false;
    expect(await transport.retryUnconfirmed()).toBe('accepted');
    await untilEnded();
    expect(canSend(store())).toBe(true);
  });

  it('429 는 Retry-After 동안 막고, 헤더가 없으면 5초 막는다', async () => {
    controls.fail('AGENT_BUSY');
    expect(await sendNow(draft())).toBe('failed');
    expect(of('sendFailed')).toMatchObject([{ code: 'AGENT_BUSY', status: 429 }]);
    expect(canSend(store(), Date.now())).toBe(false);
    expect(canSend(store(), Date.now() + 6_000)).toBe(true);

    useChatStore.setState({ blockedUntil: null });
    start({
      send: async () => {
        throw new ApiFailure(429, 'AGENT_BUSY');
      },
    });
    await sendNow(draft());
    const until = store().blockedUntil!;
    expect(until - Date.now()).toBeGreaterThan(4_000);
    expect(until - Date.now()).toBeLessThanOrEqual(5_000);
  });

  it('꺼져 있으면 꺼짐을 알리고 보내기를 놓는다', async () => {
    controls.fail('AGENT_DISABLED');
    expect(await sendNow(draft())).toBe('failed');
    expect(of('disabled')).toHaveLength(1);
    expect(store().pending).toBeNull();
  });

  it('409 실행 중이면 대화의 실행을 찾아 이어 보고, 끝나면 다시 보낼 수 있다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    // 다른 탭이 같은 대화에 보냄
    await mockApi.send({
      ...draft('다른 탭'),
      conversation_id: conversationId,
      scope_pitch_id: null,
      client_request_id: 'other-tab',
    });
    expect(await sendNow(draft('이번 탭'))).toBe('conflict');
    await flush();
    expect(store().run).not.toBeNull();
    await untilEnded();
    expect(canSend(store())).toBe(true);
    expect(await sendNow(draft('이제 됨'))).toBe('accepted');
  });

  it("'기억 없이' 면 대화를 먼저 만들고 보낸다", async () => {
    store().setMemoryOff(true);
    const created: unknown[] = [];
    start({
      createConversation: async (request) => {
        created.push(request);
        return mockApi.createConversation(request);
      },
    });
    expect(await sendNow(draft('짧게 설명해 줘, 기억해'))).toBe('accepted');
    expect(created).toMatchObject([{ memory_off: true }]);
    await untilEnded();
    expect(store().memoryOff).toBe(true);
  });
});

// ── 취소 · 복원 · 범위 ──────────────────────────────────────

describe('취소 · 복원 · 범위', () => {
  it('실행 중에 취소하면 취소로 끝나고 다시 보낼 수 있다', async () => {
    await sendNow(draft());
    await clock.advance(3_000);
    expect(await transport.cancel()).toBe(true);
    expect(of('ended').at(-1)).toMatchObject({ status: 'CANCELLED' });
    expect([store().cancelling, canSend(store())]).toEqual([null, true]);
  });

  it('취소 요청이 실패하면 취소된 것으로 보지 않고 상태를 다시 조회한다', async () => {
    start({
      cancel: async () => {
        throw new TypeError('offline');
      },
    });
    await sendNow(draft());
    await clock.advance(3_000);
    const before = runCalls.length;
    await transport.cancel();
    await flush();
    expect(store().cancelling).toBeNull();
    expect(store().run).not.toBeNull();
    expect(runCalls.length).toBeGreaterThan(before);
    await untilEnded();
    expect(of('ended').at(-1)).toMatchObject({ status: 'DONE' });
  });

  it('보내던 중 새로고침하면 미확정으로만 알리고 스스로 다시 보내지 않는다', async () => {
    let sends = 0;
    start({
      send: async () => {
        sends += 1;
        return new Promise(() => undefined);
      },
    });
    transport.send(draft()).catch(() => undefined);
    await flush();
    useChatStore.setState({ userId: null });
    store().setUser('u');
    transport.dispose();
    signals = [];
    start({
      send: async (request) => {
        sends += 1;
        return mockApi.send(request);
      },
    });
    transport.resume();
    await clock.advance(30_000);
    expect([sends, of('unconfirmed').length]).toEqual([1, 1]);
    expect(await transport.retryUnconfirmed()).toBe('accepted');
  });

  it('대화를 고르면 그 대화의 실행을 찾아 이어 조회한다', async () => {
    await sendNow(draft());
    const conversationId = store().conversationId!;
    store().startNewConversation({ pitchId: null, title: null });
    store().selectConversation(conversationId, { pitchId: null, title: null });
    expect(canSend(store())).toBe(false);
    transport.resume();
    await flush();
    expect(store().syncing).toBe(false);
    await untilEnded();
    expect(of('ended').at(-1)).toMatchObject({ status: 'DONE' });
  });

  it('있는 대화의 범위 바꾸기 — 되면 서버 값으로, 실패하면 앞 범위로 돌아간다', async () => {
    await sendNow(draft());
    await untilEnded();
    const pitch = { pitchId: '10000000-0000-4000-8000-000000000001', title: '캡스톤 최종 발표' };
    expect(await transport.changeScope(pitch)).toBe(true);
    expect(store().scope).toEqual(pitch);
    expect(await transport.changeScope({ pitchId: 'nope', title: '없는 발표' })).toBe(false);
    expect(store().scope).toEqual(pitch);
    expect(of('scopeFailed')).toMatchObject([{ code: 'NOT_FOUND' }]);
  });
});

describe('Retry-After 읽기', () => {
  it('초 숫자 · HTTP 날짜를 읽고, 잘못된 값은 null', () => {
    const now = Date.parse('2026-11-05T00:00:00Z');
    expect(retryAfterSec('5', now)).toBe(5);
    expect(retryAfterSec('Thu, 05 Nov 2026 00:00:30 GMT', now)).toBe(30);
    expect([retryAfterSec('soon', now), retryAfterSec(null, now)]).toEqual([null, null]);
  });
});
