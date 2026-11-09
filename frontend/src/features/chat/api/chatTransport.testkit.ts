/**
 * 통신 계층 시험 도구 — `chatTransport.test.ts` · `chatTransport.order.test.ts` 가 함께 씁니다.
 * 앱 코드는 이 파일을 가져다 쓰지 않습니다.
 */
import { retryAfterSec } from '@/shared/api/client';
import { chatPaths } from './chatPaths';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { RunView } from '@/types/chat';
import type { ChatApi } from './chatApi';
import type { ChatClock } from './chatTransport';

/** 시험이 돌리는 시계 — advance 로 시간을 보내면 그 사이의 타이머를 차례로 부릅니다 */
export class ManualClock implements ChatClock {
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
export const resetInflight = () => {
  inflight = 0;
};

/**
 * 한 바퀴 — setTimeout(0) 은 jsdom 에서 느려 파일 하나에 1분 가까이 걸렸습니다. Node 의 setImmediate 를 씁니다
 * (앱 tsconfig 에는 Node 타입이 없어 globalThis 로 부릅니다)
 */
const nextTurn = (globalThis as unknown as { setImmediate: (fn: () => void) => void }).setImmediate;
const tick = () => new Promise<void>((resolve) => nextTurn(resolve));

/** 진행 중인 요청이 다 끝나고 약속이 풀릴 때까지 기다림 — 끝나지 않게 붙잡은 요청이 있으면 0.05초까지만 */
export const flush = async () => {
  const deadline = Date.now() + 50;
  do for (let i = 0; i < 5; i++) await tick();
  while (inflight > 0 && Date.now() < deadline);
  for (let i = 0; i < 5; i++) await tick();
};

/** 요청 하나를 세며 부름 */
export const counted =
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
/** 목 BE 를 직접 부름 — 실패는 `ApiFailure` 로 */
export async function call<T>(path: string, init?: RequestInit): Promise<T> {
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
export const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** 목 BE 를 부르는 API — 실제 chatApi 와 같은 경로 · 모양 */
export const mockApi: ChatApi = {
  send: (request) => call(chatPaths.send, json('POST', request)),
  run: (runId, afterSeq) => call(`${chatPaths.run(runId)}?after_seq=${afterSeq}`),
  conversationRun: (id, afterSeq) => call(`${chatPaths.conversationRun(id)}?after_seq=${afterSeq}`),
  cancel: (runId) => call(chatPaths.cancel(runId), { method: 'POST' }),
  decide: (runId, actionId, request) =>
    call(chatPaths.decision(runId, actionId), json('POST', request)),
  undo: (runId, actionId) => call(chatPaths.undo(runId, actionId), { method: 'POST' }),
  listConversations: () => call(chatPaths.conversations),
  createConversation: (request) => call(chatPaths.conversations, json('POST', request)),
  changeScope: (id, pitchId) => call(chatPaths.scope(id), json('PATCH', { pitch_id: pitchId })),
  getMemory: () => call(chatPaths.memory),
  setMemoryEnabled: (enabled) => call(chatPaths.memory, json('PUT', { enabled })),
  deleteMemoryItems: () => call(chatPaths.memoryItems, { method: 'DELETE' }),
  deleteMemoryItem: (itemId) => call(chatPaths.memoryItem(itemId), { method: 'DELETE' }),
};

export const draft = (text = '비교해 줘') => ({
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  again_of_message_id: null,
  choice: null,
});

/** 직접 만드는 실행 조회 응답 — 순번마다 진행 줄 하나. `hasMore` 는 크기 때문에 나눠 남은 이벤트가 있음 */
export function runView(
  runId: string,
  conversationId: string,
  status: RunView['status'],
  seqs: number[],
  hasMore = false,
): RunView {
  return {
    run_id: runId,
    conversation_id: conversationId,
    status,
    plan: [],
    trace: [],
    events: seqs.map((seq) => ({
      event_seq: seq,
      event: 'plan' as const,
      data: { plan: [] },
      masked: false,
    })),
    next_after_seq: seqs.at(-1) ?? 0,
    has_more: hasMore,
    cards: [],
    error: null,
    retry_after_s: null,
  };
}

export const WHOLE = { pitchId: null, title: null };

export const hold = () => {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => (release = resolve));
  return { promise, release };
};

export const never = <T>() => new Promise<T>(() => undefined);

/** 붙잡아 둔 요청 하나 — 시험이 정한 때에 답하거나 실패시킵니다 */
export interface HeldCall<A extends unknown[], R> {
  args: A;
  /** 그 요청의 끊기 신호 — 통신 계층이 끊었는지 봅니다 */
  signal: AbortSignal | undefined;
  answer: (value: R) => void;
  fail: (error: unknown) => void;
  /** 붙잡지 않았다면 받았을 응답(목 BE)으로 답합니다 */
  pass: () => Promise<void>;
}

/**
 * 붙잡을 수 있는 API 한 줄 — `holding` 인 동안 부르면 응답하지 않고 쌓아 둡니다. 끊겨도 스스로 실패하지 않습니다:
 * 응답이 끊기와 엇갈려 도착하는 가장 나쁜 경우를 보기 위해서입니다. 실제 fetch 처럼 끊기면 곧바로 실패하게 하려면
 * `abortable` 을 켭니다
 */
export function line<A extends unknown[], R>(
  real: (...args: A) => Promise<R>,
  options: { abortable?: boolean } = {},
) {
  const calls: HeldCall<A, R>[] = [];
  const state = { holding: true };
  const fn = (...args: A): Promise<R> => {
    if (!state.holding) return real(...args);
    return new Promise<R>((resolve, reject) => {
      const signal = args.find((a): a is AbortSignal => a instanceof AbortSignal);
      if (options.abortable)
        signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      calls.push({
        args,
        signal,
        answer: resolve,
        fail: reject,
        pass: () => real(...args).then(resolve, reject),
      });
    });
  };
  return {
    fn,
    calls,
    /** 붙잡기를 멈춥니다 — 그 뒤 부르면 곧바로 `real` 로 */
    release: () => {
      state.holding = false;
    },
  };
}
