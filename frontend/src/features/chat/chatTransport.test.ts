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
  MAX_REREADS,
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

/** 진행 중인 요청이 다 끝나고 약속이 풀릴 때까지 기다림 — 끝나지 않게 붙잡은 요청이 있으면 0.05초까지만 */
const flush = async () => {
  const deadline = Date.now() + 50;
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

/** 직접 만드는 실행 조회 응답 — 순번마다 진행 줄 하나 */
function runView(
  runId: string,
  conversationId: string,
  status: RunView['status'],
  seqs: number[],
  nextAfterSeq: number | null = null,
): RunView {
  return {
    run_id: runId,
    conversation_id: conversationId,
    status,
    plan: [],
    trace: [],
    events: seqs.map((seq) => ({ event_seq: seq, event: 'plan' as const, data: { plan: [] } })),
    next_after_seq: nextAfterSeq,
    cards: [],
    error: null,
    retry_after_s: null,
  };
}

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

const store = () => useChatStore.getState();
let controls: MockChatControls;
let clock: ManualClock;
let transport!: ChatTransport;
let started = false;
let signals: TransportSignal[];
let api: ChatApi;
let runCalls: number[];
let online: () => void;

/** 통신 계층을 새로 만듭니다 — 앞 것은 먼저 닫습니다. 둘이 같은 저장소를 구독하면 조회가 겹칩니다 */
function start(override: Partial<ChatApi> = {}) {
  if (started) transport.dispose();
  started = true;
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

afterEach(() => {
  if (started) transport.dispose();
  started = false;
});

// ── 보내기 → 조회 ───────────────────────────────────────────

describe('보내기 → 조회', () => {
  it('보내면 접수 뒤 조회해 문장을 순서대로 한 번씩 받고, 끝나면 알린다', async () => {
    expect(await sendNow(draft())).toBe('accepted');
    await untilEnded();
    const claims = of('progress')
      .flatMap((s) => s.events)
      .flatMap((e) => (e.event === 'claim' ? [e.data.index] : []));
    expect(claims).toEqual([0, 1, 2, 3]);
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

  it('로그아웃한 뒤 도착한 조회 응답은 버린다 — 문장이 실린 응답이어도', async () => {
    let release!: () => void;
    start({
      run: async (runId) => {
        const conversationId = store().run!.conversationId;
        await new Promise<void>((resolve) => (release = resolve));
        // 붙잡아 둔 응답에 문장 줄이 있어야 거르기를 시험할 수 있습니다
        return runView(runId, conversationId, 'RUNNING', [1]);
      },
    });
    await sendNow(draft());
    store().signOut();
    release();
    await flush();
    expect(of('progress')).toHaveLength(0);
  });

  it('같은 순번이 두 번 와도 한 번만 처리한다', async () => {
    const replies = [
      [1, 1, 2],
      [2, 3],
    ];
    start({
      run: async (runId) => {
        const seqs = replies.shift();
        return runView(runId, store().run!.conversationId, seqs ? 'RUNNING' : 'DONE', seqs ?? []);
      },
    });
    await sendNow(draft());
    await untilEnded();
    const seqs = of('progress').flatMap((s) => s.events.map((e) => e.event_seq));
    expect(seqs).toEqual([1, 2, 3]);
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

// ── 첫 리뷰에서 찾은 것 ─────────────────────────────────────

const WHOLE = { pitchId: null, title: null };
const hold = () => {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => (release = resolve));
  return { promise, release };
};

describe('닫힘 · 늦은 응답 · 늦어짐 (리뷰)', () => {
  it('닫힌 통신 계층에 진행 중이던 조회 응답이 와도, 새 통신 계층만 조회해 문장을 모두 받는다', async () => {
    const first = hold();
    let held = true;
    start({
      run: async (runId, afterSeq) => {
        if (held) {
          held = false;
          await first.promise;
        }
        return mockApi.run(runId, afterSeq);
      },
    });
    await transport.send(draft());
    await flush();
    // 화면이 다시 붙어 통신 계층을 새로 만듦
    start();
    transport.resume();
    await flush();
    first.release();
    await flush();
    expect(clock.pending().length).toBeLessThanOrEqual(1);
    await untilEnded();
    const claims = of('progress')
      .flatMap((s) => s.events)
      .flatMap((e) => (e.event === 'claim' ? [e.data.index] : []));
    expect(claims).toEqual([0, 1, 2, 3]);
  });

  it('닫힌 통신 계층이 보낸 요청의 접수가 오면, 새 통신 계층이 그 실행을 조회한다', async () => {
    const gate = hold();
    start({
      send: async (request) => {
        await gate.promise;
        return mockApi.send(request);
      },
    });
    transport.send(draft()).catch(() => undefined);
    await flush();
    start();
    transport.resume();
    gate.release();
    await flush();
    expect(store().run).not.toBeNull();
    await untilEnded();
    expect(of('ended')).toHaveLength(1);
  });

  it('앞 통신 계층이 보낸 요청은 새 통신 계층이 15초를 다시 세어 미확정으로 알린다', async () => {
    start({ send: () => new Promise(() => undefined) });
    transport.send(draft()).catch(() => undefined);
    await flush();
    start();
    transport.resume();
    await clock.advance(15_000);
    expect(of('unconfirmed')).toHaveLength(1);
  });

  it('대화를 바로 바꾸면 앞 대화의 늦은 조회는 버리고 새 대화를 조회해 잠금을 푼다', async () => {
    await sendNow(draft());
    await untilEnded();
    const a = store().conversationId!;
    store().startNewConversation(WHOLE);
    await sendNow(draft());
    await untilEnded();
    const b = store().conversationId!;

    const gate = hold();
    const asked: string[] = [];
    start({
      conversationRun: async (id) => {
        asked.push(id);
        if (id === a) {
          await gate.promise;
          throw new ApiFailure(404, 'NOT_FOUND');
        }
        return mockApi.conversationRun(id);
      },
    });
    store().selectConversation(a, WHOLE);
    await flush();
    store().selectConversation(b, WHOLE);
    await flush();
    gate.release();
    await flush();
    expect(asked).toEqual([a, b]);
    expect([store().syncing, canSend(store())]).toEqual([false, true]);
    // 앞 대화의 404 를 지금 화면에 알리지 않습니다
    expect(of('conversationMissing')).toHaveLength(0);
  });

  it('지난 실행의 늦은 404 가 새 실행의 조회를 멈추지 않는다', async () => {
    const gate = hold();
    let holdRun: string | null = null;
    start({
      run: async (runId, afterSeq) => {
        if (runId === holdRun) {
          holdRun = null;
          await gate.promise;
          throw new ApiFailure(404, 'NOT_FOUND');
        }
        return mockApi.run(runId, afterSeq);
      },
    });
    const x = await sendNow(draft());
    expect(x).toBe('accepted');
    holdRun = store().run!.runId;
    await clock.advance(2_000);
    // 다른 탭이 새 대화에서 실행 Z 를 시작했고, 그 대화를 고름
    const other = await mockApi.send({
      ...draft('다른 탭'),
      conversation_id: null,
      scope_pitch_id: null,
      client_request_id: 'other-tab',
    });
    store().selectConversation(other.conversation_id, WHOLE);
    await flush();
    gate.release();
    await flush();
    expect(of('lost')).toHaveLength(0);
    await untilEnded();
    expect(of('ended').at(-1)).toMatchObject({ runId: other.run_id });
  });

  it('승인을 130초 넘게 기다려도 늦어짐으로 보지 않는다 — 승인 뒤에는 1초 간격으로 돌아온다', async () => {
    await sendNow(draft('3장 시선을 목표로 잡아 줘'));
    for (let i = 0; i < 20 && store().run?.phase !== 'WAITING_APPROVAL'; i++)
      await clock.advance(1_000);
    expect(store().run?.phase).toBe('WAITING_APPROVAL');
    await clock.advance(200_000);
    expect(of('slow')).toHaveLength(0);

    const ask = of('progress')
      .flatMap((s) => s.cards)
      .find((c) => c.type === 'ASK_CONFIRM') as Extract<
      RunView['cards'][number],
      { type: 'ASK_CONFIRM' }
    >;
    await call(
      chatPaths.decision(ask.action_id),
      json('POST', { decision: 'APPROVE', args_hash: ask.args_hash }),
    );
    // 승인 대기 중 간격(최대 15초) 안에 다시 조회해 실행 중을 보고, 그 뒤로는 1초
    await clock.advance(15_000);
    const before = runCalls.length;
    await clock.advance(1_000);
    expect(runCalls.length - before === 1 || store().run === null).toBe(true);
    await untilEnded(10_000);
    expect(of('ended').at(-1)).toMatchObject({ status: 'DONE' });
    expect(of('slow')).toHaveLength(0);
  });

  it('취소 응답이 조회보다 늦게 와도 끝남은 실행마다 한 번만 알린다', async () => {
    const gate = hold();
    start({
      cancel: async (runId) => {
        await gate.promise;
        return mockApi.cancel(runId);
      },
    });
    await sendNow(draft());
    const runX = store().run!.runId;
    const cancelling = transport.cancel();
    await flush();
    await untilEnded();
    // 조회가 먼저 끝냄 → 새로 보냄
    expect(await sendNow(draft('다음 질문'))).toBe('accepted');
    gate.release();
    await cancelling;
    await flush();
    expect(of('ended').filter((s) => s.runId === runX)).toHaveLength(1);
  });

  it('메워지지 않는 순번이 있어도 곧바로 다시 읽기는 몇 번까지만 — 그 뒤에는 보통 간격', async () => {
    let calls = 0;
    start({
      run: async (runId) => {
        calls += 1;
        return runView(runId, store().run!.conversationId, 'RUNNING', [5]);
      },
    });
    await sendNow(draft());
    for (let i = 0; i < 10; i++) await clock.advance(0);
    expect(calls).toBe(1 + MAX_REREADS);
    expect(clock.pending()[0]).toBeGreaterThan(0);
  });

  it('다음 커서가 끝없이 와도 곧바로 다시 읽기는 몇 번까지만, 끝난 실행은 끝낸다', async () => {
    let calls = 0;
    start({
      run: async (runId) => {
        calls += 1;
        return runView(runId, store().run!.conversationId, calls > 6 ? 'DONE' : 'RUNNING', [], 9);
      },
    });
    await sendNow(draft());
    for (let i = 0; i < 10; i++) await clock.advance(0);
    expect(calls).toBe(1 + MAX_REREADS);
    await untilEnded();
    expect(of('ended')).toHaveLength(1);
  });

  it('대화 상태 조회가 실패했다가 되면 회복을 알린다', async () => {
    await sendNow(draft());
    await untilEnded();
    const id = store().conversationId!;
    let fail = true;
    start({
      conversationRun: async (cid) => {
        if (fail) {
          fail = false;
          throw new TypeError('offline');
        }
        return mockApi.conversationRun(cid);
      },
    });
    store().selectConversation(id, WHOLE);
    await flush();
    expect(of('pollTrouble')).toHaveLength(1);
    await clock.advance(3_000);
    expect(of('pollRecovered')).toHaveLength(1);
    expect(store().syncing).toBe(false);
  });

  it('꺼짐이 아닌 503 은 접수되지 않은 것 — 미확정이 아니라 실패로 놓는다', async () => {
    start({
      send: async () => {
        throw new ApiFailure(503, 'SERVICE_UNAVAILABLE');
      },
    });
    expect(await sendNow(draft())).toBe('failed');
    expect([store().pending, of('unconfirmed').length]).toEqual([null, 0]);
  });

  it('Retry-After 의 잘못된 값은 null — 날짜처럼 읽혀 0초가 되지 않는다', () => {
    for (const value of ['5.5', '-1', '1.5', 'abc 1', '2026-11-05'])
      expect(retryAfterSec(value)).toBeNull();
  });
});

describe('대기 간격', () => {
  it('승인 대기 중에는 2초에서 1.5배씩 15초까지 늘려 조회한다', async () => {
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'WAITING_APPROVAL', []),
    });
    await sendNow(draft());
    await clock.advance(80_000);
    const gaps = runCalls.slice(1, 8).map((t, i) => t - runCalls[i]!);
    expect(gaps).toEqual([2_000, 3_000, 4_500, 6_750, 10_125, 15_000, 15_000]);
  });
});
