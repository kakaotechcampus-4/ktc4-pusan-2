// @vitest-environment jsdom
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => 'u' }));
import { createChatHandlers } from '@/mocks/chat';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { RunView } from '@/types/chat';
import type { ChatApi } from './chatApi';
import { canChangeScope, canSend, useChatStore } from './chatStore';
import { createChatTransport, type ChatTransport, type TransportSignal } from './chatTransport';
import {
  ManualClock,
  WHOLE,
  counted,
  draft,
  flush,
  line,
  mockApi,
  resetInflight,
  runView,
} from './chatTransport.testkit';

/**
 * 순서 시험 표 — 요청마다 "기다리는 사이에 바뀐 상황" 네 가지를 같은 틀로 확인합니다.
 *
 * | 순서 | 무엇 | 지켜야 할 것 |
 * | --- | --- | --- |
 * | 늦게 온다 | 응답이 오기 전에 대화 · 실행이 바뀜 | 지금 화면의 상태 · 신호를 바꾸지 않는다 |
 * | 두 번 온다 | 같은 요청 · 같은 응답이 두 번 | 한 번만 반영한다 (겹쳐 누르면 한 번만 보낸다) |
 * | 순서가 바뀐다 | 뒤에 보낸 것 · 더 새 상태가 먼저 옴 | 앞의 늦은 응답이 새 상태를 덮지 않는다 |
 * | 그사이 닫힌다 | 응답이 오기 전에 통신 계층이 닫힘 | 닫힌 쪽은 요청 · 타이머 · 신호를 더 내지 않고, 잠금은 새 쪽이 이어 풀 수 있다 |
 *
 * 붙잡은 요청(`line`)은 끊겨도 스스로 실패하지 않습니다 — 끊기와 응답이 엇갈려 도착하는 가장 나쁜 경우입니다.
 * 요청을 새로 더하면 여기에 그 요청의 네 칸을 더합니다.
 */

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

const store = () => useChatStore.getState();
const PITCH = { pitchId: '10000000-0000-4000-8000-000000000001', title: '캡스톤 최종 발표' };

let clock: ManualClock;
let transport!: ChatTransport;
let started = false;
let signals: TransportSignal[];
/** 통신 계층마다 번호 — 어느 통신 계층이 부른 요청인지 */
let made = 0;
let apiLog: { by: number; name: keyof ChatApi; args: unknown[] }[];

// ── 시험용 서버 — 실행 상태를 시험이 정합니다 ─────────────────────

/** 실행마다 상태 · 이벤트 순번. 없으면 RUNNING · 이벤트 없음 */
const runStatus = new Map<string, RunView['status']>();
const runSeqs = new Map<string, number[]>();
/** 실행 → 대화, 대화 → 마지막 실행 */
const conversationOf = new Map<string, string>();
const lastRunOf = new Map<string, string>();

const serverRun: ChatApi['run'] = async (runId, afterSeq) =>
  runView(
    runId,
    conversationOf.get(runId) ?? 'c-unknown',
    runStatus.get(runId) ?? 'RUNNING',
    (runSeqs.get(runId) ?? []).filter((seq) => seq > afterSeq),
  );

const serverConversationRun: ChatApi['conversationRun'] = async (conversationId) => {
  const runId = lastRunOf.get(conversationId);
  if (!runId) return null;
  return runView(runId, conversationId, runStatus.get(runId) ?? 'RUNNING', []);
};

/** 목 BE 로 보내고, 받은 실행을 시험용 서버에 적어 둡니다 */
const recordingSend: ChatApi['send'] = async (request, signal) => {
  const accepted = await mockApi.send(request, signal);
  conversationOf.set(accepted.run_id, accepted.conversation_id);
  lastRunOf.set(accepted.conversation_id, accepted.run_id);
  return accepted;
};

/** 시험용 서버에 실행 하나를 둡니다 — 대화를 골라 이어 보는 시험용 */
function seedRun(conversationId: string, runId: string, status: RunView['status'] = 'RUNNING') {
  conversationOf.set(runId, conversationId);
  lastRunOf.set(conversationId, runId);
  runStatus.set(runId, status);
}

/** 통신 계층을 새로 만듭니다 — 앞 것은 먼저 닫습니다 */
function start(override: Partial<ChatApi> = {}) {
  if (started) transport.dispose();
  started = true;
  const me = ++made;
  const api: ChatApi = {
    ...mockApi,
    send: recordingSend,
    run: serverRun,
    conversationRun: serverConversationRun,
    ...override,
  };
  const tracked = Object.fromEntries(
    (Object.keys(api) as (keyof ChatApi)[]).map((name) => [
      name,
      counted((...args: unknown[]) => {
        apiLog.push({ by: me, name, args });
        return (api[name] as (...a: unknown[]) => Promise<unknown>)(...args);
      }),
    ]),
  ) as unknown as ChatApi;
  transport = createChatTransport({ api: tracked, clock, onOnline: () => () => undefined });
  transport.subscribe((signal) => signals.push(signal));
  return me;
}

/** 지금 통신 계층을 닫습니다 — afterEach 가 다시 닫지 않게 */
function close() {
  transport.dispose();
  started = false;
}

const of = <T extends TransportSignal['type']>(type: T, from = 0) =>
  signals.slice(from).filter((s): s is Extract<TransportSignal, { type: T }> => s.type === type);

const callsOf = (name: keyof ChatApi, by?: number) =>
  apiLog.filter((c) => c.name === name && (by === undefined || c.by === by));

/** 보내고 첫 조회까지 — 실행 · 대화 ID 를 돌려줍니다 */
async function sendRunning(status: RunView['status'] = 'RUNNING') {
  const result = await transport.send(draft());
  await flush();
  expect(result).toBe('accepted');
  const run = store().run!;
  runStatus.set(run.runId, status);
  return { runId: run.runId, conversationId: run.conversationId };
}

/** 대화를 고르고 그 대화의 실행 조회까지 */
async function choose(conversationId: string) {
  store().startNewConversation(WHOLE);
  store().selectConversation(conversationId, WHOLE);
  await flush();
}

/** 실행 없는 대화 하나 — 범위 바꾸기 시험용 */
async function idleConversation() {
  const { runId, conversationId } = await sendRunning();
  // 목 BE 의 실행도 끝냅니다 — 실행 중인 대화의 범위 바꾸기는 409 입니다
  await mockApi.cancel(runId);
  runStatus.set(runId, 'DONE');
  await clock.advance(1_000);
  expect(store().run).toBeNull();
  return conversationId;
}

beforeEach(() => {
  resetInflight();
  sessionStorage.clear();
  const chat = createChatHandlers(() => 'u');
  server.resetHandlers(...chat.handlers);
  clock = new ManualClock();
  signals = [];
  apiLog = [];
  runStatus.clear();
  runSeqs.clear();
  conversationOf.clear();
  lastRunOf.clear();
  store().signOut();
  store().setUser('u');
  store().openChat(WHOLE);
});

afterEach(() => {
  if (started) close();
});

// ── 보내기 ───────────────────────────────────────────────────

describe('보내기', () => {
  it.each([
    ['접수', null],
    ['429 거절', new ApiFailure(429, 'AGENT_BUSY', 30)],
  ])('늦게 온다 — 새 대화로 옮긴 뒤 온 %s 는 반영하지 않는다', async (_name, failure) => {
    const send = line(recordingSend);
    start({ send: send.fn });
    const sending = transport.send(draft());
    await flush();
    store().startNewConversation(WHOLE);
    const mark = signals.length;
    if (failure) send.calls[0]!.fail(failure);
    else await send.calls[0]!.pass();
    expect(await sending).toBe('stale');
    await clock.advance(20_000);
    expect([store().run, store().pending, store().blockedUntil]).toEqual([null, null, null]);
    expect(callsOf('run')).toEqual([]);
    expect(signals.slice(mark)).toEqual([]);
    expect(canSend(store(), clock.t)).toBe(true);
  });

  it('두 번 온다 — [다시 확인]의 두 시도가 모두 접수돼도 실행은 하나, 받은 이벤트를 다시 받지 않는다', async () => {
    const send = line(recordingSend);
    start({ send: send.fn });
    transport.send(draft()).catch(() => undefined);
    await flush();
    await clock.advance(15_000);
    transport.retryUnconfirmed().catch(() => undefined);
    await flush();
    await send.calls[1]!.pass();
    await flush();
    const runId = store().run!.runId;
    runSeqs.set(runId, [1, 2]);
    await clock.advance(2_000);
    expect(store().run?.afterSeq).toBe(2);
    // 앞 시도의 같은 접수가 늦게 옵니다 — 같은 키라 같은 실행입니다
    await send.calls[0]!.pass();
    await clock.advance(3_000);
    expect(store().run).toMatchObject({ runId, afterSeq: 2 });
    const seqs = of('progress').flatMap((s) => s.events.map((e) => e.event_seq));
    expect(seqs).toEqual([1, 2]);
    expect(of('progress').filter((s) => s.restart)).toHaveLength(1);
  });

  it('순서가 바뀐다 — 뒤 시도가 먼저 접수되고 앞 시도가 늦게 거절돼도 실패로 놓지 않는다', async () => {
    const send = line(recordingSend);
    start({ send: send.fn });
    transport.send(draft()).catch(() => undefined);
    await flush();
    await clock.advance(15_000);
    const retry = transport.retryUnconfirmed();
    await flush();
    await send.calls[1]!.pass();
    expect(await retry).toBe('accepted');
    const runId = store().run!.runId;
    const mark = signals.length;
    send.calls[0]!.fail(new ApiFailure(422, 'VALIDATION_FAILED'));
    await flush();
    expect(store().run?.runId).toBe(runId);
    expect(of('sendFailed', mark)).toEqual([]);
    expect(of('unconfirmed', mark)).toEqual([]);
  });

  it('순서가 바뀐다 — 앞 시도의 거절이 뒤 시도의 접수보다 먼저 와도 보내기를 놓지 않는다', async () => {
    const send = line(recordingSend);
    start({ send: send.fn });
    transport.send(draft()).catch(() => undefined);
    await flush();
    await clock.advance(15_000);
    const retry = transport.retryUnconfirmed();
    await flush();
    const mark = signals.length;
    send.calls[0]!.fail(new ApiFailure(422, 'VALIDATION_FAILED'));
    await flush();
    expect(store().pending).not.toBeNull();
    expect(of('sendFailed', mark)).toEqual([]);
    await send.calls[1]!.pass();
    expect(await retry).toBe('accepted');
    expect(store().run).not.toBeNull();
  });

  it('그사이 닫힌다 — 닫힌 쪽은 타이머 · 요청을 남기지 않고, 새 통신 계층이 이어 보내 끝낼 수 있다', async () => {
    const send = line(recordingSend);
    const first = start({ send: send.fn });
    transport.send(draft()).catch(() => undefined);
    await flush();
    close();
    send.calls[0]!.fail(new TypeError('offline'));
    await flush();
    expect(clock.pending()).toEqual([]);
    const logged = apiLog.length;
    await clock.advance(30_000);
    expect(apiLog.slice(logged).filter((c) => c.by === first)).toEqual([]);
    // 새 통신 계층 — 미확정으로 알리고, 사용자가 다시 확인하면 같은 키로 보냅니다
    send.release();
    start({ send: send.fn });
    transport.resume();
    await clock.advance(15_000);
    expect(of('unconfirmed')).toHaveLength(1);
    expect(await transport.retryUnconfirmed()).toBe('accepted');
    expect(store().pending).toBeNull();
  });
});

// ── 실행 조회 ─────────────────────────────────────────────────

describe('실행 조회', () => {
  it('늦게 온다 — 새 대화로 옮긴 뒤 온 끝남 · 이벤트는 반영하지 않고 더 조회하지 않는다', async () => {
    const run = line(serverRun);
    start({ run: run.fn });
    await transport.send(draft());
    await flush();
    const { runId, afterSeq } = store().run!;
    store().startNewConversation(WHOLE);
    const mark = signals.length;
    run.calls[0]!.answer(runView(runId, conversationOf.get(runId)!, 'DONE', [1, 2]));
    await flush();
    await clock.advance(10_000);
    expect(afterSeq).toBe(0);
    expect(store().run).toBeNull();
    expect(signals.slice(mark)).toEqual([]);
    expect(run.calls).toHaveLength(1);
  });

  it('두 번 온다 — 같은 이벤트가 다시 실린 응답은 한 번만 처리한다', async () => {
    const run = line(serverRun);
    start({ run: run.fn });
    await transport.send(draft());
    await flush();
    const { runId, conversationId } = store().run!;
    run.calls[0]!.answer(runView(runId, conversationId, 'RUNNING', [1, 2]));
    await flush();
    await clock.advance(1_000);
    run.calls[1]!.answer(runView(runId, conversationId, 'RUNNING', [1, 2]));
    await flush();
    await clock.advance(1_000);
    run.calls[2]!.answer(runView(runId, conversationId, 'DONE', [1, 2]));
    await flush();
    const seqs = of('progress').flatMap((s) => s.events.map((e) => e.event_seq));
    expect(seqs).toEqual([1, 2]);
    expect(of('ended')).toHaveLength(1);
  });

  it('순서가 바뀐다 — 다른 실행으로 바뀐 뒤 온 앞 실행의 끝남이 새 실행을 끝내거나 커서를 옮기지 않는다', async () => {
    seedRun('c-1', 'r-1');
    seedRun('c-2', 'r-2');
    const held = line(serverRun);
    start({
      run: (runId, afterSeq, signal) =>
        runId === 'r-1' ? held.fn(runId, afterSeq, signal) : serverRun(runId, afterSeq),
    });
    await choose('c-1');
    expect(held.calls).toHaveLength(1);
    await choose('c-2');
    expect(store().run?.runId).toBe('r-2');
    held.calls[0]!.answer(runView('r-1', 'c-1', 'DONE', [1, 2, 3]));
    await flush();
    await clock.advance(3_000);
    expect(store().run).toMatchObject({ runId: 'r-2', afterSeq: 0 });
    expect(of('ended')).toEqual([]);
    expect(of('progress').every((s) => s.runId === 'r-2')).toBe(true);
    expect(callsOf('run').some((c) => c.args[0] === 'r-2')).toBe(true);
  });

  it('그사이 닫힌다 — 닫힌 쪽은 신호 · 요청 · 타이머를 더 내지 않고, 새 통신 계층이 끝남을 한 번 알린다', async () => {
    const run = line(serverRun);
    const first = start({ run: run.fn });
    await transport.send(draft());
    await flush();
    const { runId, conversationId } = store().run!;
    close();
    run.calls[0]!.answer(runView(runId, conversationId, 'DONE', [1]));
    await flush();
    expect(clock.pending()).toEqual([]);
    expect(of('ended')).toEqual([]);
    run.release();
    runStatus.set(runId, 'DONE');
    start({ run: run.fn });
    transport.resume();
    await clock.advance(5_000);
    expect(callsOf('run', first)).toHaveLength(1);
    expect(of('ended')).toHaveLength(1);
    expect(store().run).toBeNull();
  });
});

// ── 대화 조회 (대화 고르기 · 되살리기) ─────────────────────────────

describe('대화 조회', () => {
  it('늦게 온다 — 다른 대화를 고른 뒤 온 앞 대화의 실행은 붙이지 않는다', async () => {
    seedRun('c-1', 'r-1');
    seedRun('c-2', 'r-2');
    const lookup = line(serverConversationRun);
    start({ conversationRun: lookup.fn });
    await choose('c-1');
    await choose('c-2');
    lookup.release();
    await lookup.calls[0]!.pass();
    await clock.advance(3_000);
    expect(store()).toMatchObject({ conversationId: 'c-2', syncing: false, run: { runId: 'r-2' } });
    expect(callsOf('run').some((c) => c.args[0] === 'r-1')).toBe(false);
  });

  it('두 번 온다 — 확인 중에 여러 번 깨워도 대화 조회는 한 번에 하나, 실행은 한 번만 붙인다', async () => {
    seedRun('c-1', 'r-1');
    const lookup = line(serverConversationRun);
    start({ conversationRun: lookup.fn });
    await choose('c-1');
    transport.checkNow();
    transport.resume();
    transport.checkNow();
    await flush();
    expect(lookup.calls).toHaveLength(1);
    lookup.release();
    await lookup.calls[0]!.pass();
    await clock.advance(3_000);
    expect(callsOf('conversationRun').length).toBeLessThanOrEqual(2);
    expect(store()).toMatchObject({ syncing: false, run: { runId: 'r-1' } });
    expect(of('progress').filter((s) => s.restart)).toHaveLength(1);
  });

  it('순서가 바뀐다 — 실행 조회가 먼저 끝냈으면 늦게 온 "진행 중" 이 실행을 되살리지 않는다', async () => {
    const lookup = line(serverConversationRun);
    start({ conversationRun: lookup.fn });
    const { runId, conversationId } = await sendRunning();
    // 새로고침처럼 상태 확인과 실행 조회가 함께 돕니다 — 대화 조회는 서버가 아직 진행 중일 때 읽었습니다
    useChatStore.setState({ syncing: true });
    transport.resume();
    transport.checkNow();
    await flush();
    runStatus.set(runId, 'DONE');
    await clock.advance(1_000);
    expect(store().run).toBeNull();
    const ended = of('ended').length;
    // 늦은 서버 사본은 다시 물어도 "진행 중" 이라고 답합니다 — 표 확인 뒤 다시 조회해도 되살리지 않아야 합니다
    const stale = () => runView(runId, conversationId, 'RUNNING', []);
    lookup.calls[0]!.answer(stale());
    await flush();
    for (let i = 1; i < 4; i++) {
      lookup.calls[i]?.answer(stale());
      await clock.advance(2_000);
    }
    expect(store().run).toBeNull();
    expect(of('ended')).toHaveLength(ended);
  });

  it('그사이 닫힌다 — 닫힌 쪽은 반영하지 않고, 새 통신 계층이 상태 확인을 끝낸다', async () => {
    seedRun('c-1', 'r-1');
    const lookup = line(serverConversationRun);
    const first = start({ conversationRun: lookup.fn });
    await choose('c-1');
    close();
    await lookup.calls[0]!.pass();
    await flush();
    expect(clock.pending()).toEqual([]);
    expect(callsOf('run', first)).toEqual([]);
    lookup.release();
    start({ conversationRun: lookup.fn });
    transport.resume();
    await clock.advance(2_000);
    expect(store()).toMatchObject({ syncing: false, run: { runId: 'r-1' } });
  });
});

// ── 취소 ─────────────────────────────────────────────────────

describe('취소', () => {
  it.each([
    ['취소됨', null],
    ['4xx 거절', new ApiFailure(409, 'AGENT_STATE_INVALID')],
    ['결과 모름', new TypeError('offline')],
  ])(
    '늦게 온다 — 새 대화로 옮긴 뒤 온 %s 는 알리지 않고 새 대화를 막지 않는다',
    async (_name, failure) => {
      const cancel = line(mockApi.cancel);
      start({ cancel: cancel.fn });
      const { runId, conversationId } = await sendRunning();
      const cancelling = transport.cancel();
      await flush();
      store().startNewConversation(WHOLE);
      const mark = signals.length;
      if (failure) cancel.calls[0]!.fail(failure);
      else cancel.calls[0]!.answer(runView(runId, conversationId, 'CANCELLED', []));
      await cancelling;
      await clock.advance(5_000);
      expect(signals.slice(mark)).toEqual([]);
      expect([store().run, store().cancelling]).toEqual([null, null]);
      expect(canSend(store(), clock.t)).toBe(true);
    },
  );

  it('두 번 온다 — 겹쳐 누르면 한 번만 보내고, 취소 응답과 조회가 모두 끝남을 보여도 끝남은 한 번', async () => {
    const cancel = line(mockApi.cancel);
    start({ cancel: cancel.fn });
    const { runId, conversationId } = await sendRunning();
    const cancelling = transport.cancel();
    expect(await transport.cancel()).toBe(false);
    await flush();
    expect(cancel.calls).toHaveLength(1);
    runStatus.set(runId, 'CANCELLED');
    await clock.advance(1_000);
    cancel.calls[0]!.answer(runView(runId, conversationId, 'CANCELLED', []));
    await cancelling;
    await flush();
    expect(of('ended')).toHaveLength(1);
    expect([store().run, store().cancelling]).toEqual([null, null]);
  });

  it('순서가 바뀐다 — 조회가 먼저 끝냈으면 늦게 온 "취소 못 함" 을 알리지 않는다', async () => {
    const cancel = line(mockApi.cancel);
    start({ cancel: cancel.fn });
    const { runId, conversationId } = await sendRunning();
    const cancelling = transport.cancel();
    await flush();
    runStatus.set(runId, 'DONE');
    await clock.advance(1_000);
    expect(of('ended')).toHaveLength(1);
    cancel.calls[0]!.answer(runView(runId, conversationId, 'RUNNING', []));
    await cancelling;
    await flush();
    expect(of('cancelFailed')).toEqual([]);
    expect(of('ended')).toHaveLength(1);
    expect([store().run, store().cancelling]).toEqual([null, null]);
  });

  it('그사이 닫힌다 — 결과를 모르는 응답이어도 "취소 중" 을 풀고, 닫힌 쪽은 다시 조회하지 않는다', async () => {
    const cancel = line(mockApi.cancel);
    const first = start({ cancel: cancel.fn });
    await sendRunning();
    const cancelling = transport.cancel();
    await flush();
    close();
    const polls = callsOf('run', first).length;
    cancel.calls[0]!.fail(new TypeError('offline'));
    await cancelling;
    await clock.advance(5_000);
    expect(store().cancelling).toBeNull();
    expect(callsOf('run', first)).toHaveLength(polls);
    expect(clock.pending()).toEqual([]);
  });
});

// ── 승인 · 거절 ───────────────────────────────────────────────

describe('승인 · 거절', () => {
  it.each([
    ['실행 중', null],
    ['4xx 거절', new ApiFailure(409, 'AGENT_EXPIRED')],
  ])('늦게 온다 — 새 대화로 옮긴 뒤 온 %s 는 반영 · 안내하지 않는다', async (_name, failure) => {
    const decide = line(mockApi.decide);
    start({ decide: decide.fn });
    const { runId, conversationId } = await sendRunning('WAITING_APPROVAL');
    await clock.advance(2_000);
    expect(store().run?.phase).toBe('WAITING_APPROVAL');
    const deciding = transport.decide('a-1', 'APPROVE', 'h');
    await flush();
    store().startNewConversation(WHOLE);
    const mark = signals.length;
    if (failure) decide.calls[0]!.fail(failure);
    else decide.calls[0]!.answer(runView(runId, conversationId, 'RUNNING', []));
    expect(await deciding).toBe('stale');
    await clock.advance(5_000);
    expect(signals.slice(mark)).toEqual([]);
    expect(store().run).toBeNull();
  });

  it('두 번 온다 — 같은 카드를 겹쳐 누르면 한 번만 보낸다', async () => {
    const decide = line(mockApi.decide);
    start({ decide: decide.fn });
    const { runId, conversationId } = await sendRunning('WAITING_APPROVAL');
    await clock.advance(2_000);
    const first = transport.decide('a-1', 'APPROVE', 'h');
    expect(await transport.decide('a-1', 'APPROVE', 'h')).toBe('locked');
    expect(await transport.decide('a-1', 'REJECT', 'h')).toBe('locked');
    await flush();
    expect(decide.calls).toHaveLength(1);
    decide.calls[0]!.answer(runView(runId, conversationId, 'RUNNING', []));
    expect(await first).toBe('decided');
  });

  it('순서가 바뀐다 — 조회가 먼저 끝냈으면 늦게 온 거절 결과로 끝남을 다시 알리지 않는다', async () => {
    const decide = line(mockApi.decide);
    start({ decide: decide.fn });
    const { runId, conversationId } = await sendRunning('WAITING_APPROVAL');
    await clock.advance(2_000);
    const deciding = transport.decide('a-1', 'REJECT', 'h');
    await flush();
    runStatus.set(runId, 'DONE');
    await clock.advance(15_000);
    expect(of('ended')).toHaveLength(1);
    decide.calls[0]!.answer(runView(runId, conversationId, 'DONE', []));
    expect(await deciding).toBe('stale');
    await flush();
    expect(of('ended')).toHaveLength(1);
    expect(of('decisionFailed')).toEqual([]);
  });

  it('그사이 닫힌다 — 닫힌 쪽은 반영 · 다시 조회하지 않는다', async () => {
    const decide = line(mockApi.decide);
    const first = start({ decide: decide.fn });
    const { runId, conversationId } = await sendRunning('WAITING_APPROVAL');
    await clock.advance(2_000);
    const deciding = transport.decide('a-1', 'APPROVE', 'h');
    await flush();
    close();
    const polls = callsOf('run', first).length;
    decide.calls[0]!.answer(runView(runId, conversationId, 'RUNNING', []));
    expect(await deciding).toBe('stale');
    await clock.advance(5_000);
    expect(callsOf('run', first)).toHaveLength(polls);
    expect(clock.pending()).toEqual([]);
  });
});

// ── 범위 바꾸기 · 범위 맞추기 ─────────────────────────────────────

describe('범위 바꾸기', () => {
  it.each([
    ['바뀜', null],
    ['4xx 거절', new ApiFailure(404, 'CONVERSATION_NOT_FOUND')],
    ['결과 모름', new TypeError('offline')],
  ])(
    '늦게 온다 — 새 대화로 옮긴 뒤 온 %s 는 새 대화의 칩을 바꾸지 않는다',
    async (_name, failure) => {
      const change = line(mockApi.changeScope);
      start({ changeScope: change.fn });
      const conversationId = await idleConversation();
      const changing = transport.changeScope(PITCH);
      await flush();
      store().startNewConversation(WHOLE);
      const mark = signals.length;
      if (failure) change.calls[0]!.fail(failure);
      else await change.calls[0]!.pass();
      expect(await changing).toBe(false);
      await flush();
      expect(store()).toMatchObject({ conversationId: null, scope: WHOLE, scopeChange: null });
      expect(signals.slice(mark)).toEqual([]);
      expect(canChangeScope(store())).toBe(true);
      expect(conversationId).not.toBeNull();
    },
  );

  it('두 번 온다 — 바꾸는 중에 다시 누르면 보내지 않는다', async () => {
    const change = line(mockApi.changeScope);
    start({ changeScope: change.fn });
    await idleConversation();
    const changing = transport.changeScope(PITCH);
    expect(await transport.changeScope(WHOLE)).toBe(false);
    await flush();
    expect(change.calls).toHaveLength(1);
    await change.calls[0]!.pass();
    expect(await changing).toBe(true);
    expect(store().scope).toEqual(PITCH);
  });

  it('순서가 바뀐다 — 실행 뒤 범위 맞추기가 늦게 와도 그사이 사용자가 바꾼 칩을 되돌리지 않는다', async () => {
    const list = line(mockApi.listConversations);
    start({ listConversations: list.fn });
    await idleConversation();
    expect(list.calls).toHaveLength(1);
    expect(await transport.changeScope(PITCH)).toBe(true);
    // 늦은 목록은 바꾸기 전의 범위(전체)를 싣고 있습니다
    list.calls[0]!.answer({
      conversations: [
        { conversation_id: store().conversationId, scope_pitch_id: null, scope_pitch_title: null },
      ],
    } as never);
    await flush();
    expect(store().scope).toEqual(PITCH);
  });

  it.each([
    ['바뀜', null, PITCH],
    ['4xx 거절', new ApiFailure(404, 'CONVERSATION_NOT_FOUND'), WHOLE],
  ])(
    '그사이 닫힌다 — %s 응답으로 잠금을 풀고, 닫힌 쪽은 안내 · 요청을 더 내지 않는다',
    async (_name, failure, scope) => {
      const change = line(mockApi.changeScope);
      const first = start({ changeScope: change.fn });
      await idleConversation();
      const changing = transport.changeScope(PITCH);
      await flush();
      close();
      const logged = apiLog.length;
      if (failure) change.calls[0]!.fail(failure);
      else await change.calls[0]!.pass();
      await changing;
      await flush();
      expect(store()).toMatchObject({ scope, scopeChange: null });
      expect(apiLog.slice(logged).filter((c) => c.by === first)).toEqual([]);
      expect(clock.pending()).toEqual([]);
    },
  );
});

// ── 대화 만들기 ('기억 없이') ─────────────────────────────────────

describe('대화 만들기', () => {
  const memoryOff = () => {
    store().startNewConversation(WHOLE);
    expect(store().setMemoryOff(true)).toBe(true);
  };

  it.each([
    ['만든 대화', null],
    ['거절', new ApiFailure(422, 'VALIDATION_FAILED')],
  ])('늦게 온다 — 새 대화로 옮긴 뒤 온 %s 는 붙이지 않고 보내지 않는다', async (_name, failure) => {
    const create = line(mockApi.createConversation);
    start({ createConversation: create.fn });
    memoryOff();
    const sending = transport.send(draft());
    await flush();
    store().startNewConversation(WHOLE);
    const mark = signals.length;
    if (failure) create.calls[0]!.fail(failure);
    else await create.calls[0]!.pass();
    expect(await sending).not.toBe('accepted');
    await flush();
    expect(store()).toMatchObject({ conversationId: null, creating: null, pending: null });
    expect(callsOf('send')).toEqual([]);
    expect(signals.slice(mark)).toEqual([]);
  });

  it('두 번 온다 — 만드는 중에 다시 보내면 대화를 또 만들지 않는다', async () => {
    const create = line(mockApi.createConversation);
    start({ createConversation: create.fn });
    memoryOff();
    const first = transport.send(draft());
    expect(await transport.send(draft('또'))).toBe('locked');
    await flush();
    expect(create.calls).toHaveLength(1);
    await create.calls[0]!.pass();
    expect(await first).toBe('accepted');
    expect(callsOf('send')).toHaveLength(1);
  });

  it('순서가 바뀐다 — 뒤에 만든 대화가 먼저 오고 앞 대화가 늦게 와도 뒤 대화에 그대로 있다', async () => {
    const create = line(mockApi.createConversation);
    start({ createConversation: create.fn });
    memoryOff();
    const firstSend = transport.send(draft('앞'));
    await flush();
    memoryOff();
    const secondSend = transport.send(draft('뒤'));
    await flush();
    expect(create.calls).toHaveLength(2);
    await create.calls[1]!.pass();
    expect(await secondSend).toBe('accepted');
    const conversationId = store().conversationId;
    await create.calls[0]!.pass();
    expect(await firstSend).not.toBe('accepted');
    await flush();
    expect(store().conversationId).toBe(conversationId);
    expect(callsOf('send')).toHaveLength(1);
  });

  it('그사이 닫힌다 — 만든 대화는 붙여 잠금을 풀되 닫힌 쪽은 보내지 않고, 새 통신 계층에서 보낼 수 있다', async () => {
    const create = line(mockApi.createConversation);
    start({ createConversation: create.fn });
    memoryOff();
    const sending = transport.send(draft());
    await flush();
    close();
    await create.calls[0]!.pass();
    expect(await sending).not.toBe('accepted');
    await flush();
    expect(store().creating).toBeNull();
    expect(store().conversationId).not.toBeNull();
    expect(callsOf('send')).toEqual([]);
    expect(clock.pending()).toEqual([]);
    start();
    expect(await transport.send(draft())).toBe('accepted');
  });
});
