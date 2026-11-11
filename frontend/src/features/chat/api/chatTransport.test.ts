// @vitest-environment jsdom
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => 'u' }));
import { createChatHandlers, type MockChatControls } from '@/mocks/chat';
import { ResponseTooLargeError, retryAfterSec } from '@/shared/api/client';
import { chatPaths } from './chatPaths';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { RunView } from '@/types/chat';
import type { ChatApi } from './chatApi';
import { MAX_RESPONSE_BYTES } from '../rules/chatResponse';
import { canSend, useChatStore } from '../store/chatStore';
import {
  MAX_REREADS,
  MAX_TOO_LARGE,
  REQUEST_TIMEOUT_MS,
  STATUS_ONLY_AFTER_SEQ,
  createChatTransport,
  type ChatTransport,
  type TransportSignal,
} from './chatTransport';
import {
  ManualClock,
  WHOLE,
  call,
  counted,
  draft,
  flush,
  hold,
  json,
  mockApi,
  never,
  resetInflight,
  runView,
} from './chatTransport.testkit';

// ── 시험 도구 ───────────────────────────────────────────────

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
    run: counted((runId: string, afterSeq: number, signal?: AbortSignal) => {
      runCalls.push(clock.t);
      return api.run(runId, afterSeq, signal);
    }),
    conversationRun: counted(api.conversationRun),
    cancel: counted(api.cancel),
    decide: counted(api.decide),
    createConversation: counted(api.createConversation),
    changeScope: counted(api.changeScope),
    undo: counted(api.undo),
    listConversations: counted(api.listConversations),
    getMemory: counted(api.getMemory),
    setMemoryEnabled: counted(api.setMemoryEnabled),
    deleteMemoryItems: counted(api.deleteMemoryItems),
    deleteMemoryItem: counted(api.deleteMemoryItem),
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
  resetInflight();
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
      .flatMap((e) => (e.event === 'claim' && e.data ? [e.data.index] : []));
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
      masked: false,
    });
    const view = (status: RunView['status'], seqs: number[]): RunView => ({
      run_id: store().run!.runId,
      conversation_id: store().run!.conversationId,
      status,
      plan: [],
      trace: [],
      events: seqs.map(plan),
      next_after_seq: seqs.at(-1) ?? 0,
      has_more: false,
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
        has_more: false,
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
    // 목도 BE 처럼 자정까지의 Retry-After 를 줍니다 — 그때까지 막습니다
    expect(canSend(store(), Date.now())).toBe(false);
    expect(canSend(store(), Date.now() + 86_400_000)).toBe(true);

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
      client_request_id: crypto.randomUUID(),
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
    expect(
      await transport.changeScope({
        pitchId: '99999999-0000-4000-8000-000000000000',
        title: '없는 발표',
      }),
    ).toBe(false);
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
      .flatMap((e) => (e.event === 'claim' && e.data ? [e.data.index] : []));
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
        return mockApi.conversationRun(id, 0);
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
      client_request_id: crypto.randomUUID(),
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

    // 진행 신호의 카드는 응답 검사를 거친 값 — 그려도 되는 카드만 card 를 갖습니다
    const ask = of('progress')
      .flatMap((s) => s.cards)
      .flatMap((c) => (c.ok && c.card.type === 'ASK_CONFIRM' ? [c.card] : []))[0]!;
    await call(
      chatPaths.decision(store().run!.runId, ask.action_id),
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

  it('남은 이벤트(has_more)가 끝없이 와도 곧바로 다시 읽기는 몇 번까지만, 끝난 실행은 끝낸다', async () => {
    let calls = 0;
    start({
      run: async (runId) => {
        calls += 1;
        return runView(
          runId,
          store().run!.conversationId,
          calls > 6 ? 'DONE' : 'RUNNING',
          [],
          true,
        );
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
        return mockApi.conversationRun(cid, 0);
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

// ── BE #933 모양 ────────────────────────────────────────────

/** BE `RunStatusResponse` 그대로 — 이벤트 · 카드 · 다음 커서 칸이 없고, 진행이 없으면 계획 · 기록이 null */
const beRun = (
  runId: string,
  conversationId: string,
  status: RunView['status'],
  plan: unknown = null,
) => ({
  run_id: runId,
  conversation_id: conversationId,
  status,
  plan,
  trace: null,
  error: null,
  retry_after_s: null,
});

describe('BE #933 모양의 실행 조회', () => {
  it('이벤트 칸이 없어도 형식 오류가 아니다 — 계획이 바뀔 때만 진행을 알리고, 끝나면 끝남을 알린다', async () => {
    let polls = 0;
    const plan = [{ step: '기록 읽기', status: 'DOING', note: null }];
    start({
      run: async (runId) => {
        polls += 1;
        const conversationId = store().run!.conversationId;
        if (polls < 3) return beRun(runId, conversationId, 'RUNNING', polls === 1 ? null : plan);
        if (polls < 5) return beRun(runId, conversationId, 'RUNNING', plan);
        return beRun(runId, conversationId, 'DONE', plan);
      },
    });
    await sendNow(draft());
    await untilEnded();
    expect(of('pollTrouble')).toEqual([]);
    // 계획이 처음 생긴 한 번만 — 같은 계획을 다시 받으면 알리지 않습니다
    expect(of('progress')).toMatchObject([
      { events: [], claims: [], plan, trace: null, cards: [], progressDropped: false },
    ]);
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
    expect(store().run).toBeNull();
  });

  it('칸이 있는데 모양이 틀리면 형식 오류로 센다', async () => {
    start({
      run: async (runId) => ({
        ...beRun(runId, store().run!.conversationId, 'RUNNING'),
        events: 'nope',
      }),
    });
    await sendNow(draft());
    expect(of('pollTrouble')).toMatchObject([{ failures: 1, reason: 'FORMAT' }]);
  });

  it('응답이 2 MiB 를 넘으면 크기 초과로 알리고 간격을 늘려 다시 조회한다 (L3-22)', async () => {
    let polls = 0;
    start({
      run: async (runId) => {
        polls += 1;
        if (polls === 1) throw new ResponseTooLargeError(MAX_RESPONSE_BYTES);
        return beRun(runId, store().run!.conversationId, 'DONE');
      },
    });
    await sendNow(draft());
    expect(of('pollTrouble')).toMatchObject([{ failures: 1, reason: 'TOO_LARGE' }]);
    await untilEnded();
    expect(of('pollRecovered')).toHaveLength(1);
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
  });

  it('대화의 마지막 실행이 끝나 있으면 이어 가지 않고, 끝나지 않았으면 이어 간다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    const ended = of('ended').length;
    /** 대화를 다시 골라 그 대화의 실행을 조회하게 합니다 */
    const reopen = async (found: ReturnType<typeof beRun>) => {
      start({
        conversationRun: async () => found,
        run: async (runId) => beRun(runId, conversationId, 'RUNNING'),
      });
      store().startNewConversation(WHOLE);
      store().selectConversation(conversationId, WHOLE);
      await flush();
    };

    await reopen(beRun('r-done', conversationId, 'DONE'));
    expect([store().run, store().syncing, canSend(store())]).toEqual([null, false, true]);

    await reopen(beRun('r-live', conversationId, 'RUNNING'));
    expect([store().run?.runId, store().run?.phase]).toEqual(['r-live', 'RUNNING']);
    expect(of('ended')).toHaveLength(ended);
  });

  it('다른 대화의 실행을 돌려주면 형식 오류로 보고 반영하지 않는다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    start({ conversationRun: async () => beRun('r-x', 'another', 'RUNNING') });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    expect([store().run, store().syncing]).toEqual([null, true]);
    expect(of('pollTrouble')).toHaveLength(1);
  });

  it('새 대화 본문은 BE 모양(`pitch_id`) — 연 화면은 싣지 않는다', async () => {
    const created: unknown[] = [];
    store().setMemoryOff(true);
    start({
      createConversation: async (request) => {
        created.push(request);
        return mockApi.createConversation(request);
      },
    });
    await sendNow(draft());
    expect(created).toEqual([{ pitch_id: null, memory_off: true }]);
  });
});

describe('보내기 키', () => {
  it('randomUUID 가 없는 곳(보안 연결 아님)에서도 UUID 모양으로 만든다', async () => {
    const sent: string[] = [];
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    try {
      start({
        send: async (request) => {
          sent.push(request.client_request_id);
          return mockApi.send(request);
        },
      });
      expect(await sendNow(draft())).toBe('accepted');
    } finally {
      vi.unstubAllGlobals();
    }
    expect(sent[0]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});

// ── 응답 검사 연결 (4단계 점검) ─────────────────────────────

describe('진행 신호의 문장 읽기', () => {
  const claimsOf = () => of('progress').flatMap((s) => s.claims);

  it('실행 중에 통신 계층을 새로 만들어도 문장을 처음부터 다시 받아 순번 오류가 나지 않는다', async () => {
    await sendNow(draft());
    for (let i = 0; i < 30 && claimsOf().length < 2; i++) await clock.advance(1_000);
    expect(claimsOf().length).toBeGreaterThan(0);
    expect(store().run!.afterSeq).toBeGreaterThan(0);

    // 팝업을 다시 열어 통신 계층이 새로 생김 — 저장소의 커서는 남아 있습니다
    signals.length = 0;
    start();
    transport.resume();
    await flush();
    await untilEnded();
    expect(of('progress').map((s) => s.contractError)).not.toContain('INDEX_GAP');
    expect(claimsOf().map((c) => c.index)).toEqual([0, 1, 2, 3]);
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
  });

  it('다른 대화로 갔다 돌아오면 그 실행의 문장을 처음부터 다시 보내고 restart 로 알린다', async () => {
    await sendNow(draft());
    for (let i = 0; i < 30 && claimsOf().length < 2; i++) await clock.advance(1_000);
    const first = of('progress');
    expect(first[0]!.restart).toBe(true);
    expect(first.slice(1).every((s) => !s.restart)).toBe(true);
    const conversationId = store().conversationId!;
    signals.length = 0;
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    await untilEnded();
    // 화면은 이 실행의 문장을 비우고 다시 그립니다 — 그 뒤로는 같은 문장이 두 번 오지 않습니다
    const again = of('progress');
    expect(again[0]!.restart).toBe(true);
    expect(claimsOf().map((c) => c.index)).toEqual([0, 1, 2, 3]);
    expect(again.map((s) => s.contractError)).not.toContain('INDEX_GAP');
  });

  it('[다시 확인]은 받은 문장을 다시 보내지 않는다 — 늦어짐만 처음부터 다시 센다', async () => {
    await sendNow(draft());
    for (let i = 0; i < 30 && claimsOf().length < 2; i++) await clock.advance(1_000);
    const seen = claimsOf().map((c) => c.index);
    transport.checkNow();
    await flush();
    await untilEnded();
    const all = claimsOf().map((c) => c.index);
    expect(all.slice(0, seen.length)).toEqual(seen);
    expect(all).toEqual([0, 1, 2, 3]);
    expect(of('progress').filter((s) => s.restart)).toHaveLength(1);
  });

  it('모르는 종류의 문장이 오면 계약 오류를 알리고 그 뒤 문장은 보내지 않는다', async () => {
    await sendNow(draft('모르는 문장'));
    await untilEnded();
    expect(claimsOf().map((c) => c.kind)).toEqual(['RECORD']);
    expect(of('progress').at(-1)!.contractError).toBe('UNKNOWN_KIND');
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
  });

  it('깨진 할 일 항목은 빼고 알린다', async () => {
    start({
      run: async (runId) => ({
        run_id: runId,
        conversation_id: store().run!.conversationId,
        status: 'RUNNING',
        plan: [{ step: '비교', status: 'DOING', note: null }, { step: '' }],
        trace: null,
        error: null,
        retry_after_s: null,
      }),
    });
    await sendNow(draft());
    expect(of('progress')).toMatchObject([
      { plan: [{ step: '비교' }], trace: null, progressDropped: true },
    ]);
  });
});

// ── 1~4단계 점검 반영 ────────────────────────────────────────

describe('응답이 오지 않는 요청 (L3-19)', () => {
  it('실행 조회가 걸려도 20초 뒤 실패로 세고 다시 조회해 끝까지 간다', async () => {
    let polls = 0;
    start({
      run: (runId, afterSeq) => {
        polls += 1;
        return polls === 2 ? never() : mockApi.run(runId, afterSeq);
      },
    });
    await sendNow(draft());
    for (let i = 0; i < 5 && polls < 2; i++) await clock.advance(1_000);
    expect(polls).toBe(2);
    await clock.advance(REQUEST_TIMEOUT_MS);
    expect(of('pollTrouble')).toMatchObject([{ failures: 1, reason: 'TIMEOUT' }]);
    await untilEnded();
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
    expect(canSend(store())).toBe(true);
  });

  it('대화 조회 · 범위 바꾸기 · 대화 만들기가 걸려도 잠금이 풀린다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;

    start({ conversationRun: () => never(), changeScope: () => never() });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    expect(store().syncing).toBe(true);
    await clock.advance(REQUEST_TIMEOUT_MS);
    expect(of('pollTrouble').at(-1)).toMatchObject({ reason: 'TIMEOUT' });

    start({ changeScope: () => never() });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    await clock.advance(1_000);
    expect(store().syncing).toBe(false);
    const changing = transport.changeScope({
      pitchId: '10000000-0000-4000-8000-000000000001',
      title: '캡스톤 최종 발표',
    });
    await flush();
    await clock.advance(REQUEST_TIMEOUT_MS);
    expect(await changing).toBe(false);
    expect([store().scopeChange, store().scope]).toEqual([null, WHOLE]);

    start({ createConversation: () => never() });
    store().startNewConversation(WHOLE);
    store().setMemoryOff(true);
    const sending = transport.send(draft());
    await flush();
    await clock.advance(REQUEST_TIMEOUT_MS);
    expect(await sending).toBe('failed');
    expect(store().creating).toBeNull();
  });
});

describe('서버가 취소하지 않고 끝나지 않은 상태를 돌려줌 — 예: 다른 탭이 먼저 승인해 RUNNING', () => {
  it('"취소 중" 을 풀고 알린다 — 보내기 · 다시 취소가 막히지 않는다', async () => {
    start({
      run: async (runId) => beRun(runId, store().run!.conversationId, 'WAITING_APPROVAL'),
      cancel: async (runId) => beRun(runId, store().run!.conversationId, 'WAITING_APPROVAL'),
    });
    await sendNow(draft());
    expect(store().run?.phase).toBe('WAITING_APPROVAL');
    expect(await transport.cancel()).toBe(true);
    await flush();
    expect(store().cancelling).toBeNull();
    expect(of('cancelFailed')).toHaveLength(1);
    expect(canSend(store())).toBe(true);
  });
});

describe('로그인이 풀림 (L3-04)', () => {
  it('조회가 401 이면 알리고 더 두드리지 않는다', async () => {
    start({
      run: async () => {
        throw new ApiFailure(401, 'SESSION_CHANGED');
      },
    });
    await sendNow(draft());
    const calls = runCalls.length;
    await clock.advance(120_000);
    expect(of('sessionExpired')).toHaveLength(1);
    expect(of('pollTrouble')).toEqual([]);
    expect(runCalls.length).toBe(calls);
  });
});

describe('503 AGENT_NOT_ACCEPTED — BE 는 같은 키로 다시 보내라고 정함', () => {
  it('미확정으로 두고, [다시 확인]은 같은 client_request_id 로 보낸다', async () => {
    const keys: string[] = [];
    start({
      send: async (request) => {
        keys.push(request.client_request_id);
        if (keys.length === 1) throw new ApiFailure(503, 'AGENT_NOT_ACCEPTED');
        return mockApi.send(request);
      },
    });
    expect(await sendNow(draft())).toBe('unconfirmed');
    expect(of('sendFailed')).toEqual([]);
    // 서버가 접수하지 못했다고 알렸으니 "접수하지 못했으니 다시 보내 주세요" 로 가를 수 있게 알립니다
    expect(of('unconfirmed').map((s) => s.notAccepted)).toEqual([true]);
    expect(await transport.retryUnconfirmed()).toBe('accepted');
    expect(new Set(keys).size).toBe(1);
  });
});

describe('늦게 온 실패 · 대화 바꾸기', () => {
  it('다른 대화로 옮긴 뒤 늦게 온 범위 바꾸기 실패는 안내하지 않는다', async () => {
    await sendNow(draft());
    await untilEnded();
    let fail!: () => void;
    start({
      changeScope: () =>
        new Promise((_, reject) => (fail = () => reject(new ApiFailure(500, 'INTERNAL')))),
    });
    const changing = transport.changeScope({
      pitchId: '10000000-0000-4000-8000-000000000001',
      title: '캡스톤 최종 발표',
    });
    await flush();
    store().startNewConversation(WHOLE);
    fail();
    expect(await changing).toBe(false);
    expect(of('scopeFailed')).toEqual([]);
  });

  it('대화 조회 실패를 기다리는 중에 다른 대화를 고르면 바로 그 대화를 조회한다', async () => {
    await sendNow(draft());
    await untilEnded();
    const good = store().conversationId!;
    const asked: string[] = [];
    start({
      conversationRun: async (id) => {
        asked.push(id);
        if (id === 'c-bad') throw new ApiFailure(500, 'INTERNAL');
        return mockApi.conversationRun(id, 0);
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation('c-bad', WHOLE);
    await flush();
    await clock.advance(2_500);
    expect(asked).toEqual(['c-bad', 'c-bad']);
    store().selectConversation(good, WHOLE);
    await flush();
    expect(asked.at(-1)).toBe(good);
    expect(store().syncing).toBe(false);
  });
});

// ── 새 BE (37e3a9df) 에 맞춤 ──────────────────────────────────

describe('이벤트 커서 — next_after_seq 는 늘 다음 커서, 남은 것은 has_more', () => {
  it('BE 처럼 다음 커서가 늘 와도 곧바로 다시 읽지 않고 실행 중 1초 간격을 지킨다', async () => {
    let seq = 0;
    start({
      run: async (runId) => {
        seq += 1;
        return {
          ...runView(runId, store().run!.conversationId, 'RUNNING', [seq]),
          next_after_seq: seq,
          has_more: false,
        };
      },
    });
    await sendNow(draft());
    await clock.advance(5_000);
    const gaps = runCalls.slice(1).map((t, i) => t - runCalls[i]!);
    expect(gaps.every((g) => g === 1_000)).toBe(true);
    expect(runCalls.length).toBeLessThanOrEqual(6);
  });

  it('대화의 실행을 찾을 때는 이벤트 없이 상태만 받는 커서를 보낸다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    const cursors: number[] = [];
    start({
      conversationRun: async (id, afterSeq) => {
        cursors.push(afterSeq);
        return mockApi.conversationRun(id, afterSeq);
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    expect(cursors).toEqual([STATUS_ONLY_AFTER_SEQ]);
  });
});

describe('가린 문장 줄 (근거가 지워짐)', () => {
  const claimData = (index: number) => ({
    index,
    claim: {
      kind: 'SYSTEM',
      assessment_id: null,
      text: `문장 ${index}`,
      evidence_ids: [],
      source_ids: [],
      action_id: null,
    },
    evidence: [],
    sources: [],
    assessments: [],
  });

  it('가린 줄은 자리를 채우는 안내로 보내고, 뒤 문장은 그대로 이어진다', async () => {
    start({
      run: async (runId, afterSeq) => {
        const conversationId = store().run!.conversationId;
        const all = [
          { event_seq: 1, event: 'claim', data: null, masked: true },
          { event_seq: 2, event: 'claim', data: claimData(1), masked: false },
        ].filter((e) => e.event_seq > afterSeq);
        return {
          ...runView(runId, conversationId, afterSeq >= 2 ? 'DONE' : 'RUNNING', []),
          events: all,
          next_after_seq: all.at(-1)?.event_seq ?? afterSeq,
        };
      },
    });
    await sendNow(draft());
    await untilEnded();
    const claims = of('progress').flatMap((s) => s.claims);
    expect(claims.map((c) => [c.index, c.masked, c.text])).toEqual([
      [0, true, '근거 기록이 지워져 이 답을 가렸어요.'],
      [1, false, '문장 1'],
    ]);
    expect(of('progress').map((s) => s.contractError)).not.toContain('BAD_CLAIM');
    expect(of('pollTrouble')).toEqual([]);
  });

  it('가렸다면서 내용이 있으면 형식 오류', async () => {
    start({
      run: async (runId) => ({
        ...runView(runId, store().run!.conversationId, 'RUNNING', []),
        events: [{ event_seq: 1, event: 'claim', data: claimData(0), masked: true }],
      }),
    });
    await sendNow(draft());
    expect(of('pollTrouble')).toMatchObject([{ reason: 'FORMAT' }]);
  });

  it('가리지 않았는데 내용이 없으면 형식 오류', async () => {
    start({
      run: async (runId) => ({
        ...runView(runId, store().run!.conversationId, 'RUNNING', []),
        events: [{ event_seq: 1, event: 'claim', data: null, masked: false }],
      }),
    });
    await sendNow(draft());
    expect(of('pollTrouble')).toMatchObject([{ reason: 'FORMAT' }]);
  });
});

describe('승인 · 거절 (L3-39 · L3-40)', () => {
  /** 목의 승인 대기까지 — 진행 신호의 확인 카드 */
  async function untilAsk() {
    await sendNow(draft('3장 시선을 목표로 잡아 줘'));
    for (let i = 0; i < 30 && store().run?.phase !== 'WAITING_APPROVAL'; i++)
      await clock.advance(1_000);
    return of('progress')
      .flatMap((s) => s.cards)
      .flatMap((c) => (c.ok && c.card.type === 'ASK_CONFIRM' ? [c.card] : []))
      .at(-1)!;
  }

  it('[승인]하면 실행 중으로 돌아가 끝까지 받고, 같은 카드를 겹쳐 누르면 막는다', async () => {
    const ask = await untilAsk();
    const first = transport.decide(ask.action_id, 'APPROVE', ask.args_hash);
    expect(await transport.decide(ask.action_id, 'APPROVE', ask.args_hash)).toBe('locked');
    expect(await first).toBe('decided');
    expect(store().run?.phase).toBe('RUNNING');
    await untilEnded();
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
  });

  it('[거절]하면 DONE 으로 끝난다', async () => {
    const ask = await untilAsk();
    expect(await transport.decide(ask.action_id, 'REJECT', ask.args_hash)).toBe('decided');
    expect(of('ended')).toMatchObject([{ status: 'DONE' }]);
    expect(store().run).toBeNull();
  });

  it('카드가 바뀌었으면(409) 알리고 상태를 다시 읽는다 — 승인 대기가 아니면 보내지 않는다', async () => {
    const ask = await untilAsk();
    const before = runCalls.length;
    expect(await transport.decide(ask.action_id, 'APPROVE', 'stale-hash')).toBe('failed');
    expect(of('decisionFailed')).toMatchObject([
      // BE 는 바뀐 카드에 409 APPROVAL_HASH_MISMATCH 를 줍니다 — 그대로 알립니다
      { actionId: ask.action_id, code: 'APPROVAL_HASH_MISMATCH' },
    ]);
    await flush();
    expect(runCalls.length).toBeGreaterThan(before);
    store().endRun(store().run!.runId);
    expect(await transport.decide(ask.action_id, 'APPROVE', ask.args_hash)).toBe('locked');
  });
});

describe('결과를 모르는 취소 · 범위 바꾸기 (L3-34)', () => {
  it('취소 응답을 잃으면 "확인 중" 으로 두고, 다음 조회가 끝나지 않았으면 취소 못 함으로 확정한다', async () => {
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'RUNNING', []),
      cancel: async () => {
        throw new TypeError('offline');
      },
    });
    await sendNow(draft());
    expect(await transport.cancel()).toBe(true);
    expect(of('cancelChecking')).toHaveLength(1);
    expect(of('cancelFailed')).toEqual([]);
    expect(store().cancelling).not.toBeNull();
    await flush();
    await clock.advance(1_000);
    expect(of('cancelFailed')).toHaveLength(1);
    expect(store().cancelling).toBeNull();
  });

  it('취소 응답을 잃었어도 서버가 취소했으면 다음 조회의 끝남으로 확정한다', async () => {
    let cancelled = false;
    start({
      run: async (runId) =>
        runView(runId, store().run!.conversationId, cancelled ? 'CANCELLED' : 'RUNNING', []),
      cancel: async () => {
        cancelled = true;
        throw new TypeError('offline');
      },
    });
    await sendNow(draft());
    await transport.cancel();
    await flush();
    await clock.advance(1_000);
    expect(of('cancelFailed')).toEqual([]);
    expect(of('ended')).toMatchObject([{ status: 'CANCELLED' }]);
  });

  it('범위 바꾸기 응답을 잃으면 서버의 범위를 다시 읽어 맞춘다', async () => {
    await sendNow(draft());
    await untilEnded();
    const pitch = { pitchId: '10000000-0000-4000-8000-000000000001', title: '캡스톤 최종 발표' };
    // 서버는 바꿨는데 응답을 잃음
    start({
      changeScope: async (id, pitchId) => {
        await mockApi.changeScope(id, pitchId);
        throw new TypeError('offline');
      },
    });
    expect(await transport.changeScope(pitch)).toBe(true);
    expect(store().scope).toEqual(pitch);
    expect(of('scopeFailed')).toEqual([]);
    // 서버가 받지 못함 — 앞 범위로
    start({
      changeScope: async () => {
        throw new TypeError('offline');
      },
    });
    expect(await transport.changeScope(WHOLE)).toBe(false);
    expect(store().scope).toEqual(pitch);
    expect(of('scopeFailed')).toHaveLength(1);
  });
});

describe('걸린 요청 끊기', () => {
  it('시간이 넘은 조회는 요청을 끊는다', async () => {
    const seen: AbortSignal[] = [];
    start({
      run: (_runId, _afterSeq, signal) => {
        seen.push(signal!);
        return never();
      },
    });
    await sendNow(draft());
    expect(seen[0]!.aborted).toBe(false);
    await clock.advance(REQUEST_TIMEOUT_MS);
    expect(seen[0]!.aborted).toBe(true);
  });

  it('걸린 대화 조회 중에 다른 대화를 고르면 끊고 곧바로 새 대화를 조회한다', async () => {
    await sendNow(draft());
    await untilEnded();
    const good = store().conversationId!;
    const asked: string[] = [];
    start({
      conversationRun: (id, afterSeq, signal) => {
        asked.push(id);
        if (id !== 'c-hung') return mockApi.conversationRun(id, afterSeq);
        return new Promise((_, reject) =>
          signal!.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          ),
        );
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation('c-hung', WHOLE);
    await flush();
    store().selectConversation(good, WHOLE);
    await flush();
    expect(asked).toEqual(['c-hung', good]);
    expect(store().syncing).toBe(false);
  });
});

// ── 문서 #900 · #948 대조 뒤 고친 것 ──────────────────────────

describe('스위치가 꺼짐 (503 AGENT_DISABLED)', () => {
  const off = () => new ApiFailure(503, 'AGENT_DISABLED');

  it('실행 조회가 꺼짐을 받으면 알리고 더 조회하지 않는다', async () => {
    start({
      run: async () => {
        throw off();
      },
    });
    await sendNow(draft());
    const calls = runCalls.length;
    await clock.advance(120_000);
    expect(of('disabled')).toHaveLength(1);
    expect(of('pollTrouble')).toEqual([]);
    expect(runCalls.length).toBe(calls);
  });

  it('대화 조회 · 취소 · 범위 바꾸기에서도 꺼짐을 알린다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    start({
      conversationRun: async () => {
        throw off();
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    expect(of('disabled')).toHaveLength(1);
    expect(of('pollTrouble')).toEqual([]);

    start({
      changeScope: async () => {
        throw off();
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    expect(
      await transport.changeScope({
        pitchId: '10000000-0000-4000-8000-000000000001',
        title: '캡스톤 최종 발표',
      }),
    ).toBe(false);
    expect(of('disabled')).toHaveLength(2);
    expect(of('scopeFailed')).toEqual([]);

    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'RUNNING', []),
      cancel: async () => {
        throw off();
      },
    });
    await sendNow(draft());
    await transport.cancel();
    expect(of('disabled')).toHaveLength(3);
    expect(store().cancelling).toBeNull();
    expect(of('cancelChecking')).toEqual([]);
  });
});

describe('조회 간격 — 접수 대기 · 승인 대기 모두 2초', () => {
  it('접수 대기가 길어도 2초마다 조회한다', async () => {
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'QUEUED', []),
    });
    await sendNow(draft());
    await clock.advance(20_000);
    const gaps = runCalls.slice(1).map((t, i) => t - runCalls[i]!);
    expect(new Set(gaps)).toEqual(new Set([2_000]));
  });

  it('승인 대기가 길어도 늘리지 않고 2초마다 조회한다', async () => {
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'WAITING_APPROVAL', []),
    });
    await sendNow(draft());
    await clock.advance(30_000);
    const gaps = runCalls.slice(1).map((t, i) => t - runCalls[i]!);
    expect(new Set(gaps)).toEqual(new Set([2_000]));
  });
});

describe('끝남 신호 · 범위 맞추기', () => {
  it('승인 대기에서 곧바로 끝나면 endedWhileWaiting, 실행 중에 끝나면 아니다', async () => {
    let status: RunView['status'] = 'WAITING_APPROVAL';
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, status, []),
      cancel: async (runId) => runView(runId, store().run!.conversationId, 'CANCELLED', []),
    });
    await sendNow(draft());
    expect(store().run?.phase).toBe('WAITING_APPROVAL');
    await transport.cancel();
    expect(of('ended')).toMatchObject([{ status: 'CANCELLED', endedWhileWaiting: true }]);

    // 새 대화에서 새 실행 — 같은 대화로 보내면 목의 앞 실행 때문에 409 가 나서, 끝낸 실행을 다시 찾게 됩니다
    status = 'RUNNING';
    store().startNewConversation(WHOLE);
    await sendNow(draft());
    status = 'DONE';
    await untilEnded();
    expect(of('ended').at(-1)).toMatchObject({ status: 'DONE', endedWhileWaiting: false });
  });

  it('실행이 끝나면 서버가 확정한 범위로 칩을 맞춘다 (에이전트가 다른 피치를 연 경우)', async () => {
    const pitch = { pitchId: '10000000-0000-4000-8000-000000000001', title: '캡스톤 최종 발표' };
    start({
      listConversations: async () => ({
        conversations: [
          {
            conversation_id: store().conversationId,
            scope_pitch_id: pitch.pitchId,
            scope_pitch_title: pitch.title,
          },
        ],
      }),
    });
    expect(store().scope).toEqual(WHOLE);
    await sendNow(draft());
    await untilEnded();
    await flush();
    expect(store().scope).toEqual(pitch);
  });

  it('범위를 읽는 사이 다른 대화로 옮겼으면 맞추지 않는다', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    start({
      listConversations: async () => {
        await gate;
        return {
          conversations: [
            {
              conversation_id: 'old',
              scope_pitch_id: '10000000-0000-4000-8000-000000000001',
              scope_pitch_title: 'x',
            },
          ],
        };
      },
    });
    await sendNow(draft());
    await untilEnded();
    store().startNewConversation(WHOLE);
    release();
    await flush();
    expect(store().scope).toEqual(WHOLE);
  });
});

describe('승인 · 거절의 결과를 모를 때 (L3-39)', () => {
  it('응답을 잃으면 같은 요청으로 한 번 다시 보낸다', async () => {
    const sent: unknown[] = [];
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'WAITING_APPROVAL', []),
      decide: async (runId, _actionId, request) => {
        sent.push(request);
        if (sent.length === 1) throw new TypeError('offline');
        return runView(runId, store().run!.conversationId, 'RUNNING', []);
      },
    });
    await sendNow(draft());
    expect(await transport.decide('a-1', 'APPROVE', 'h')).toBe('decided');
    expect(sent).toEqual([
      { decision: 'APPROVE', args_hash: 'h' },
      { decision: 'APPROVE', args_hash: 'h' },
    ]);
    expect(of('decisionFailed')).toEqual([]);
  });

  it('서버가 거절했으면(4xx) 다시 보내지 않는다', async () => {
    let calls = 0;
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'WAITING_APPROVAL', []),
      decide: async () => {
        calls += 1;
        throw new ApiFailure(409, 'AGENT_EXPIRED');
      },
    });
    await sendNow(draft());
    expect(await transport.decide('a-1', 'APPROVE', 'h')).toBe('failed');
    expect(calls).toBe(1);
    expect(of('decisionFailed')).toMatchObject([{ code: 'AGENT_EXPIRED' }]);
  });
});

// ── 글자 효과를 줄지 (5단계, 12-4 F12) ───────────────────────

describe('따라잡는 묶음(catchUp)', () => {
  const withClaims = () => of('progress').filter((s) => s.claims.length > 0);
  async function untilClaims(n = 1) {
    for (let i = 0; i < 30 && withClaims().flatMap((s) => s.claims).length < n; i++)
      await clock.advance(1_000);
  }

  it('이 화면에서 보낸 실행은 처음부터 효과로 보인다', async () => {
    await sendNow(draft());
    await untilEnded();
    expect(withClaims().map((s) => s.catchUp)).toEqual(withClaims().map(() => false));
  });

  it('첫 응답에 문장이 없어도 따라잡기는 첫 문장 묶음까지 이어진다', async () => {
    await sendNow(draft());
    await untilClaims(2);
    let polls = 0;
    signals.length = 0;
    start({
      // 다시 연 뒤 첫 응답은 할 일 목록만 — 문장은 그다음에 옵니다
      run: async (runId, afterSeq) => {
        polls += 1;
        const view = (await mockApi.run(runId, afterSeq)) as RunView;
        return polls === 1
          ? {
              ...view,
              events: view.events.filter((e) => e.event !== 'claim').slice(0, 1),
              has_more: true,
            }
          : view;
      },
    });
    transport.resume();
    await flush();
    await untilClaims(1);
    expect(withClaims()[0]!.catchUp).toBe(true);
  });

  it('화면을 다시 열어(새 통신 계층) 이어 받으면 이미 있던 문장은 효과 없이', async () => {
    await sendNow(draft());
    await untilClaims(2);
    signals.length = 0;
    start();
    transport.resume();
    await flush();
    const first = withClaims()[0]!;
    expect([first.restart, first.catchUp]).toEqual([true, true]);
  });

  it('다른 대화로 갔다 돌아와도 효과 없이 다시 그린다', async () => {
    await sendNow(draft());
    await untilClaims(2);
    const conversationId = store().conversationId!;
    signals.length = 0;
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    await untilClaims(1);
    const first = withClaims()[0]!;
    expect([first.restart, first.catchUp]).toEqual([true, true]);
    // 따라잡은 뒤 새로 오는 문장은 다시 효과로
    await untilEnded();
    expect(
      withClaims()
        .slice(1)
        .every((s) => !s.catchUp),
    ).toBe(true);
  });
});

// ── 1~5단계 최종 점검 반영 ───────────────────────────────────

describe('늦게 온 대화 조회가 끝난 실행을 되살리지 않는다 (L3-20)', () => {
  it('조회를 시작한 뒤 실행이 끝났으면 그 응답을 버리고, 끝낸 실행은 다시 붙이지 않는다', async () => {
    await sendNow(draft());
    for (let i = 0; i < 5 && store().run?.phase !== 'RUNNING'; i++) await clock.advance(1_000);
    const runId = store().run!.runId;
    const conversationId = store().conversationId!;
    // 새로고침처럼 상태 확인을 켜고, 대화 조회는 붙잡아 둡니다(서버는 아직 실행 중일 때 읽음)
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    start({
      conversationRun: async () => {
        await gate;
        return runView(runId, conversationId, 'RUNNING', []);
      },
      run: async (id) => runView(id, conversationId, 'DONE', []),
    });
    // 새로고침처럼 실행 조회와 대화 조회가 함께 돕니다 — 대화 조회는 붙잡혀 있고, 실행 조회가 먼저 끝남을 받습니다
    useChatStore.setState({ syncing: true });
    transport.resume();
    transport.checkNow();
    await flush();
    await clock.advance(1_000);
    expect(store().run).toBeNull();
    const ended = of('ended').length;
    release();
    await flush();
    await clock.advance(5_000);
    expect(store().run).toBeNull();
    expect(of('ended')).toHaveLength(ended);
  });
});

describe('범위 맞추기와 사용자의 범위 바꾸기가 겹칠 때', () => {
  it('목록을 읽는 사이 사용자가 바꾼 범위를 늦은 목록이 되돌리지 않는다', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const B = { pitchId: '10000000-0000-4000-8000-000000000001', title: '캡스톤 최종 발표' };
    start({
      listConversations: async () => {
        await gate;
        return {
          conversations: [
            {
              conversation_id: store().conversationId,
              scope_pitch_id: null,
              scope_pitch_title: null,
            },
          ],
        };
      },
    });
    await sendNow(draft());
    await untilEnded();
    expect(await transport.changeScope(B)).toBe(true);
    release();
    await flush();
    expect(store().scope).toEqual(B);
  });
});

describe('닫힌 뒤 온 취소 응답', () => {
  it('결과를 모르는 응답이어도 "취소 중" 을 풀어, 새 통신 계층에서 다시 취소할 수 있다', async () => {
    let fail!: () => void;
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'RUNNING', []),
      cancel: () => new Promise((_, reject) => (fail = () => reject(new TypeError('offline')))),
    });
    await sendNow(draft());
    const cancelling = transport.cancel();
    await flush();
    expect(store().cancelling).not.toBeNull();
    start({ run: async (runId) => runView(runId, store().run!.conversationId, 'RUNNING', []) });
    transport.resume();
    fail();
    await cancelling;
    expect(store().cancelling).toBeNull();
  });

  it('닫힌 뒤 끝남 응답이 오면 실행을 끝낸다', async () => {
    let done!: () => void;
    start({
      run: async (runId) => runView(runId, store().run!.conversationId, 'RUNNING', []),
      cancel: (runId) =>
        new Promise((resolve) => {
          const conversationId = store().run!.conversationId;
          done = () => resolve(runView(runId, conversationId, 'CANCELLED', []));
        }),
    });
    await sendNow(draft());
    const cancelling = transport.cancel();
    await flush();
    transport.dispose();
    started = false;
    done();
    await cancelling;
    expect([store().run, store().cancelling]).toEqual([null, null]);
  });
});

describe('실행이 바뀌면 걸린 실행 조회를 끊는다', () => {
  it('다른 대화의 실행으로 바뀌면 앞 실행의 걸린 조회를 기다리지 않고 새 실행을 조회한다', async () => {
    await sendNow(draft());
    await untilEnded();
    const first = store().conversationId!;
    store().startNewConversation(WHOLE);
    await sendNow(draft());
    await untilEnded();
    const second = store().conversationId!;
    const asked: string[] = [];
    start({
      conversationRun: async (id) => runView(id === first ? 'r-1' : 'r-2', id, 'RUNNING', []),
      run: (runId, _afterSeq, signal) => {
        asked.push(runId);
        if (runId === 'r-1')
          return new Promise((_, reject) =>
            signal!.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            ),
          );
        return Promise.resolve(runView(runId, second, 'RUNNING', []));
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation(first, WHOLE);
    await flush();
    expect(asked).toEqual(['r-1']);
    // 시간을 흘리지 않고도(20초 시간 초과를 기다리지 않고) 곧바로 새 실행을 조회합니다
    const t0 = clock.t;
    store().selectConversation(second, WHOLE);
    await flush();
    expect(asked).toContain('r-2');
    expect(clock.t).toBe(t0);
  });
});

describe('[다시 확인]을 거듭 누를 때', () => {
  it('앞 보내기를 끊고, 앞 시도의 늦은 실패가 뒤 시도의 미확정 타이머를 지우지 않는다', async () => {
    const seen: AbortSignal[] = [];
    const rejects: ((e: unknown) => void)[] = [];
    start({
      send: (_request, signal) => {
        seen.push(signal!);
        return new Promise((_, reject) => rejects.push(reject));
      },
    });
    transport.send(draft()).catch(() => undefined);
    await flush();
    await clock.advance(15_000);
    expect(of('unconfirmed')).toHaveLength(1);
    transport.retryUnconfirmed().catch(() => undefined);
    await flush();
    expect(seen[0]!.aborted).toBe(true);
    expect(seen[1]!.aborted).toBe(false);
    // 앞 시도가 늦게 실패해도 뒤 시도는 그대로 — 15초가 지나야 미확정이 다시 뜹니다
    rejects[0]!(new TypeError('offline'));
    await flush();
    expect(of('unconfirmed')).toHaveLength(1);
    await clock.advance(15_000);
    expect(of('unconfirmed')).toHaveLength(2);
  });
});

describe('꺼짐 · 로그인 만료로 멈춘 뒤 연결 복귀', () => {
  it('연결이 돌아와도 다시 두드리지 않고, resume 으로만 다시 시작한다', async () => {
    let off = true;
    start({
      run: async (runId) => {
        if (off) throw new ApiFailure(503, 'AGENT_DISABLED');
        return runView(runId, store().run!.conversationId, 'RUNNING', []);
      },
    });
    await sendNow(draft());
    const calls = runCalls.length;
    online();
    await flush();
    expect(runCalls.length).toBe(calls);
    off = false;
    transport.resume();
    await flush();
    expect(runCalls.length).toBe(calls + 1);
  });
});

describe('다시 해도 같은 거절 (#953 리뷰)', () => {
  it.each([403, 400, 422])(
    '실행 조회가 %i 이면 다시 조회하지 않고 놓은 뒤 lost 로 알린다',
    async (status) => {
      start({
        run: async () => {
          throw new ApiFailure(status, 'X');
        },
      });
      await sendNow(draft());
      const calls = runCalls.length;
      await clock.advance(120_000);
      expect(runCalls.length).toBe(calls);
      expect(of('lost')).toHaveLength(1);
      expect(of('pollTrouble')).toEqual([]);
      expect(store().run).toBeNull();
    },
  );

  it.each([408, 429, 500])('실행 조회가 %i 이면 간격을 늘려 다시 조회한다', async (status) => {
    start({
      run: async () => {
        throw new ApiFailure(status, 'X');
      },
    });
    await sendNow(draft());
    const calls = runCalls.length;
    await clock.advance(10_000);
    expect(runCalls.length).toBeGreaterThan(calls);
    expect(of('lost')).toEqual([]);
    expect(of('pollTrouble').length).toBeGreaterThan(0);
  });

  it('대화 조회가 403 이면 상태 확인을 풀고 다시 조회하지 않는다', async () => {
    await sendNow(draft());
    await untilEnded();
    const conversationId = store().conversationId!;
    let lookups = 0;
    start({
      conversationRun: async () => {
        lookups += 1;
        throw new ApiFailure(403, 'X');
      },
    });
    store().startNewConversation(WHOLE);
    store().selectConversation(conversationId, WHOLE);
    await flush();
    await clock.advance(60_000);
    expect(lookups).toBe(1);
    expect(store().syncing).toBe(false);
    expect(of('conversationMissing')).toMatchObject([{ conversationId }]);
    expect(of('pollTrouble')).toEqual([]);
  });
});

describe('크기 초과가 같은 커서에서 계속될 때 (#955 리뷰)', () => {
  it('MAX_TOO_LARGE 번 잇달아 넘으면 실행을 놓고 lost 로 넘긴다 — 30초 간격으로 끝없이 다시 읽지 않는다', async () => {
    start({
      run: async () => {
        throw new ResponseTooLargeError(MAX_RESPONSE_BYTES);
      },
    });
    await sendNow(draft());
    await clock.advance(120_000);
    expect(of('pollTrouble').map((s) => s.reason)).toEqual(
      Array(MAX_TOO_LARGE - 1).fill('TOO_LARGE'),
    );
    expect(of('lost')).toHaveLength(1);
    expect(store().run).toBeNull();
    const calls = runCalls.length;
    await clock.advance(120_000);
    expect(runCalls.length).toBe(calls);
  });

  it('사이에 조회가 되면 다시 처음부터 센다', async () => {
    let polls = 0;
    start({
      run: async (runId) => {
        polls += 1;
        if (polls % 2 === 1) throw new ResponseTooLargeError(MAX_RESPONSE_BYTES);
        return runView(runId, store().run!.conversationId, 'RUNNING', []);
      },
    });
    await sendNow(draft());
    await clock.advance(60_000);
    expect(of('lost')).toEqual([]);
    expect(store().run).not.toBeNull();
  });
});
