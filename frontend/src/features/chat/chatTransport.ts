import { ApiFailure } from '@/shared/api/tokenStore';
import { isTerminalRun } from '@/shared/lib/chatAgent';
import type { Card, PlanStep, RunEvent, RunStatus, RunView, TraceItem } from '@/types/chat';
import { chatApi, type ChatApi } from './chatApi';
import {
  chatTicket,
  isCurrent,
  needsCreate,
  pendingSendTicket,
  useChatStore,
  type ChatScope,
  type ChatTicket,
  type RunPhase,
  type SendDraft,
} from './chatStore';

/**
 * 대화 에이전트 통신 계층 (`docs/ai/features/chat-agent.md` 12-4 F3 · 7-5 L3-13 ~ L3-21 · L3-34 · L3-46).
 *
 * 저장소(`chatStore`)가 "지금 해도 되는가" 를 정하면, 여기서 서버를 부르고 응답을 저장소에 알립니다.
 * 화면은 `subscribe` 로 신호(받은 문장 · 끝남 · 미확정 · 오류)를 받아 그립니다.
 *
 * 지키는 규칙
 * - 보내기는 **자동으로 다시 보내지 않습니다**(L3-18 "사용자 재시도만"). 15초 안에 접수가 확정되지 않으면 미확정으로 알리고,
 *   사용자가 [다시 확인]을 누르면 같은 키 · 본문으로 다시 보냅니다(`retryUnconfirmed`, L3-15).
 * - 조회는 한 번에 하나(L3-19). 실행 중 1초, 그 밖에는 2초부터 늘리고, 실패하면 2 · 4 · 8 · 16 · 30초(±20%).
 *   연결이 돌아오면 바로 조회합니다. 형식이 틀린 응답도 실패로 봅니다.
 * - 같은 순번은 한 번만 처리하고, 건너뛴 순번이 있으면 같은 커서로 다시 읽습니다(L3-46).
 * - 130초가 지나도 끝나지 않으면 자동 조회를 늦추고 알립니다 — 화면은 `checkNow` 로 직접 확인하게 합니다(L3-21).
 * - 404 는 실행 기록이 없어진 것 — 실행을 놓고 알립니다. 화면은 메시지 목록을 다시 읽어 복구할 답을 찾습니다(L3-21).
 * - 늦게 온 응답은 저장소의 표(`isCurrent`)로 버립니다(L3-20).
 */

/** 화면이 받는 신호 */
export type TransportSignal =
  /** 새로 받은 진행 줄 · 문장 줄과 지금 실행 모습 */
  | {
      type: 'progress';
      runId: string;
      conversationId: string;
      status: RunStatus;
      events: RunEvent[];
      plan: PlanStep[];
      trace: TraceItem[];
      /** 승인 대기 중 카드 */
      cards: Card[];
    }
  /** 실행이 끝남 — 화면은 메시지 목록을 다시 읽습니다 */
  | {
      type: 'ended';
      runId: string;
      conversationId: string;
      status: RunStatus;
      errorCode: string | null;
    }
  /** 15초 넘게 접수를 확인하지 못함 — "전송 여부를 확인하지 못했으니 다시 확인해 주세요." */
  | { type: 'unconfirmed'; sendId: string }
  /** 보내기 · 대화 만들기가 거절됨 — 코드별 안내(F8) */
  | { type: 'sendFailed'; code: string; status: number }
  /** 꺼짐 — 버튼을 숨깁니다 */
  | { type: 'disabled' }
  /** 조회가 잇따라 실패 — "진행 상태를 확인하지 못해 다시 연결하고 있어요." */
  | { type: 'pollTrouble'; failures: number }
  | { type: 'pollRecovered' }
  /** 130초가 지나도 끝나지 않음 — "결과 확인이 늦어지고 있으니 상태를 다시 확인해 주세요." */
  | { type: 'slow'; runId: string }
  /** 실행 기록을 찾을 수 없음(404) — 메시지 목록을 다시 읽어 복구합니다 */
  | { type: 'lost'; runId: string }
  /** 대화를 찾을 수 없음 — "대화를 찾을 수 없어요." */
  | { type: 'conversationMissing'; conversationId: string }
  /** 범위 바꾸기 실패 — 칩은 앞 범위로 돌아갔습니다 */
  | { type: 'scopeFailed'; code: string };

export type SendResult =
  'accepted' | 'locked' | 'unconfirmed' | 'conflict' | 'failed' | 'stale' | 'none';

/** 시간 — 시험에서 바꿔 끼웁니다 */
export interface ChatClock {
  now: () => number;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  random: () => number;
}

export interface ChatTransportOptions {
  api?: ChatApi;
  clock?: ChatClock;
  /** 연결이 돌아왔을 때 부를 함수를 등록하고, 해제 함수를 돌려줍니다 */
  onOnline?: (listener: () => void) => () => void;
}

export interface ChatTransport {
  /** 보내기 — '기억 없이' 새 대화면 대화부터 만듭니다 */
  send: (draft: SendDraft) => Promise<SendResult>;
  /** 미확정인 보내기를 같은 키 · 본문으로 다시 보냅니다 — 사용자가 누를 때만 */
  retryUnconfirmed: () => Promise<SendResult>;
  /** 대화를 고르거나 되살린 뒤 · 화면에 돌아왔을 때 — 상태를 읽고 조회를 이어 갑니다 */
  resume: () => void;
  /** [취소] */
  cancel: () => Promise<boolean>;
  /** 범위 칩 — 새 대화는 바로, 있는 대화는 서버에 바꾸기를 요청합니다 */
  changeScope: (scope: ChatScope) => Promise<boolean>;
  /** 사용자가 직접 확인 — 늦어진 실행 · 조회 실패 뒤 */
  checkNow: () => void;
  subscribe: (listener: (signal: TransportSignal) => void) => () => void;
  dispose: () => void;
}

export const SEND_UNCONFIRMED_MS = 15_000;
export const POLL_RUNNING_MS = 1_000;
/** 그 밖(대기 · 승인 대기)은 2초에서 1.5배씩, 15초까지 — 문서는 "2초 · 늘림" 만 정해 늘리는 폭은 FE 가 정했습니다 */
export const POLL_IDLE_START_MS = 2_000;
export const POLL_IDLE_MAX_MS = 15_000;
export const FAILURE_STEPS_MS = [2_000, 4_000, 8_000, 16_000, 30_000];
export const SLOW_AFTER_MS = 130_000;
export const SLOW_POLL_MS = 30_000;
/** 429 에 `Retry-After` 가 없거나 잘못됐을 때 (L3-18) */
export const DEFAULT_RETRY_AFTER_SEC = 5;

const defaultClock: ChatClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  random: () => Math.random(),
};

const defaultOnOnline = (listener: () => void) => {
  if (typeof window === 'undefined') return () => undefined;
  window.addEventListener('online', listener);
  return () => window.removeEventListener('online', listener);
};

const PHASES: readonly RunStatus[] = ['QUEUED', 'RUNNING', 'WAITING_APPROVAL'];
const KNOWN: readonly RunStatus[] = [
  ...PHASES,
  'DONE',
  'PARTIAL',
  'REFUSED',
  'FAILED',
  'CANCELLED',
  'EXPIRED',
];

/** 실행 조회 응답의 최소 형식 — 틀리면 실패로 봅니다(L3-19). 세부 검사는 응답 검사(4단계)가 맡습니다 */
function isRunView(v: unknown): v is RunView {
  if (typeof v !== 'object' || v === null) return false;
  const r = v as RunView;
  return (
    typeof r.run_id === 'string' &&
    typeof r.status === 'string' &&
    Array.isArray(r.events) &&
    r.events.every((e) => typeof e === 'object' && e !== null && Number.isInteger(e.event_seq)) &&
    Array.isArray(r.plan) &&
    Array.isArray(r.trace) &&
    Array.isArray(r.cards)
  );
}

/** 아직 처리하지 않은 이벤트를 순번대로, 건너뛴 순번 앞까지만 (L3-46) */
function takeEvents(afterSeq: number, events: RunEvent[]) {
  const sorted = [...events].sort((a, b) => a.event_seq - b.event_seq);
  const taken: RunEvent[] = [];
  let expected = afterSeq + 1;
  for (const event of sorted) {
    if (event.event_seq < expected) continue;
    if (event.event_seq > expected) return { taken, gap: true };
    taken.push(event);
    expected += 1;
  }
  return { taken, gap: false };
}

const asPhase = (status: RunStatus): RunPhase =>
  (PHASES.includes(status) ? status : 'RUNNING') as RunPhase;

export function createChatTransport(options: ChatTransportOptions = {}): ChatTransport {
  const api = options.api ?? chatApi;
  const clock = options.clock ?? defaultClock;
  const store = () => useChatStore.getState();
  const listeners = new Set<(signal: TransportSignal) => void>();
  const emit = (signal: TransportSignal) => {
    for (const listener of listeners) listener(signal);
  };

  // ── 조회 고리 ───────────────────────────────────────────────
  let pollTimer: unknown = null;
  let polling = false;
  let pollAgain = false;
  let failures = 0;
  let idleDelay = 0;
  const startedAt = new Map<string, number>();
  const slowNotified = new Set<string>();

  // 대화 상태 조회는 따로 한 번에 하나
  let lookupTimer: unknown = null;
  let lookingUp = false;
  let lookupFailures = 0;

  const clearPoll = () => {
    if (pollTimer !== null) clock.clearTimeout(pollTimer);
    pollTimer = null;
  };

  const schedulePoll = (ms: number) => {
    clearPoll();
    pollTimer = clock.setTimeout(() => {
      pollTimer = null;
      pollOnce().catch(() => undefined);
    }, ms);
  };

  /** 지금 바로 조회 — 조회 중이면 끝난 뒤 한 번 더 */
  const kick = () => {
    clearPoll();
    if (polling) pollAgain = true;
    else pollOnce().catch(() => undefined);
  };

  const watch = (runId: string) => {
    if (!startedAt.has(runId)) startedAt.set(runId, clock.now());
    idleDelay = 0;
  };

  const failureDelay = (count: number) => {
    const base = FAILURE_STEPS_MS[Math.min(count, FAILURE_STEPS_MS.length) - 1]!;
    return Math.round(base * (0.8 + clock.random() * 0.4));
  };

  const nextDelay = (runId: string, status: RunStatus) => {
    const started = startedAt.get(runId) ?? clock.now();
    if (clock.now() - started >= SLOW_AFTER_MS) {
      if (!slowNotified.has(runId)) {
        slowNotified.add(runId);
        emit({ type: 'slow', runId });
      }
      return SLOW_POLL_MS;
    }
    if (status === 'RUNNING') {
      idleDelay = 0;
      return POLL_RUNNING_MS;
    }
    idleDelay = idleDelay === 0 ? POLL_IDLE_START_MS : Math.min(idleDelay * 1.5, POLL_IDLE_MAX_MS);
    return idleDelay;
  };

  const finishRun = (runId: string, conversationId: string, view: RunView) => {
    store().endRun(runId, view.retry_after_s);
    startedAt.delete(runId);
    slowNotified.delete(runId);
    idleDelay = 0;
    emit({
      type: 'ended',
      runId,
      conversationId,
      status: view.status,
      errorCode: view.error?.code ?? null,
    });
  };

  async function pollOnce(): Promise<void> {
    const run = store().run;
    if (!run || polling) return;
    polling = true;
    pollAgain = false;
    const ticket = chatTicket();
    let view: RunView;
    try {
      const response: unknown = await api.run(run.runId, run.afterSeq);
      if (!isRunView(response) || response.run_id !== run.runId) throw new Error('형식 오류');
      view = response;
    } catch (error) {
      polling = false;
      onPollFailure(run.runId, error);
      return;
    }
    polling = false;
    // 그사이 실행 · 대화 · 세대가 바뀌었으면 이 응답은 버리고 지금 상태로 다시 (L3-20)
    if (!isCurrent(ticket) || pollAgain) return kick();
    if (failures > 0) {
      failures = 0;
      emit({ type: 'pollRecovered' });
    }

    const { taken, gap } = takeEvents(run.afterSeq, view.events);
    const last = taken.at(-1);
    if (last) store().advanceCursor(run.runId, last.event_seq);
    if (taken.length > 0 || view.cards.length > 0)
      emit({
        type: 'progress',
        runId: run.runId,
        conversationId: run.conversationId,
        status: view.status,
        events: taken,
        plan: view.plan,
        trace: view.trace,
        cards: view.cards,
      });
    // 건너뛴 순번 · 나눠 보낸 나머지는 같은 커서로 곧바로 다시 읽습니다
    if (gap || view.next_after_seq !== null) return schedulePoll(0);
    if (isTerminalRun(view.status)) return finishRun(run.runId, run.conversationId, view);
    // 모르는 상태는 "확인 중" — 끝나지 않은 것으로 보고 계속 조회합니다(L3-22)
    if (KNOWN.includes(view.status)) store().setRunPhase(run.runId, asPhase(view.status));
    schedulePoll(nextDelay(run.runId, view.status));
  }

  function onPollFailure(runId: string, error: unknown) {
    if (error instanceof ApiFailure && error.status === 404) {
      // 실행 기록이 없어짐 — 놓고 목록에서 복구합니다(L3-21)
      store().endRun(runId);
      startedAt.delete(runId);
      emit({ type: 'lost', runId });
      return;
    }
    failures += 1;
    emit({ type: 'pollTrouble', failures });
    schedulePoll(failureDelay(failures));
  }

  // ── 대화 상태 조회 (고르기 · 되살리기 · 409) ───────────────────

  const scheduleLookup = (ms: number) => {
    if (lookupTimer !== null) clock.clearTimeout(lookupTimer);
    lookupTimer = clock.setTimeout(() => {
      lookupTimer = null;
      lookup().catch(() => undefined);
    }, ms);
  };

  async function lookup(): Promise<void> {
    const s = store();
    if (lookingUp || !s.syncing || !s.conversationId) return;
    lookingUp = true;
    const ticket = chatTicket();
    const conversationId = s.conversationId;
    try {
      const response = await api.conversationRun(conversationId);
      const run = response?.run ?? null;
      if (run !== null && !isRunView(run)) throw new Error('형식 오류');
      lookingUp = false;
      lookupFailures = 0;
      const found =
        run && !isTerminalRun(run.status)
          ? { runId: run.run_id, phase: asPhase(run.status) }
          : null;
      if (store().resolveConversation(ticket, found) && found) {
        watch(found.runId);
        kick();
      }
    } catch (error) {
      lookingUp = false;
      if (error instanceof ApiFailure && error.status === 404) {
        store().resolveConversation(ticket, null);
        emit({ type: 'conversationMissing', conversationId });
        return;
      }
      lookupFailures += 1;
      emit({ type: 'pollTrouble', failures: lookupFailures });
      scheduleLookup(failureDelay(lookupFailures));
    }
  }

  // ── 보내기 ─────────────────────────────────────────────────

  function onSendFailure(ticket: ChatTicket, error: unknown): SendResult {
    const unconfirmed = (): SendResult => {
      store().markUnconfirmed(ticket);
      emit({ type: 'unconfirmed', sendId: ticket.sendId! });
      return 'unconfirmed';
    };
    // 네트워크 끊김 · 시간 초과 — 서버가 받았는지 모릅니다(L3-15)
    if (!(error instanceof ApiFailure)) return unconfirmed();
    const { status, code } = error;
    if (status === 409 && code === 'AGENT_RUN_IN_PROGRESS') {
      // 다른 탭이 실행 중 — 상태를 조회해 그 실행을 이어 봅니다(L3-17)
      store().conflictSend(ticket);
      lookup().catch(() => undefined);
      return 'conflict';
    }
    if (status === 429) {
      store().failSend(ticket, error.retryAfterSec ?? DEFAULT_RETRY_AFTER_SEC);
      emit({ type: 'sendFailed', code, status });
      return 'failed';
    }
    if (status === 503 && code === 'AGENT_DISABLED') {
      store().failSend(ticket);
      emit({ type: 'disabled' });
      return 'failed';
    }
    // 503 은 커밋 전 실패(12-3 "커밋 전 실패는 503") — 접수되지 않았습니다. 그 밖 5xx 는 받았는지 모릅니다
    if (status >= 500 && status !== 503) return unconfirmed();
    store().failSend(ticket);
    emit({ type: 'sendFailed', code, status });
    return 'failed';
  }

  async function post(ticket: ChatTicket): Promise<SendResult> {
    const pending = store().pending;
    if (!pending || pending.sendId !== ticket.sendId) return 'stale';
    const timer = clock.setTimeout(() => {
      if (store().pending?.sendId !== ticket.sendId) return;
      store().markUnconfirmed(ticket);
      emit({ type: 'unconfirmed', sendId: ticket.sendId! });
    }, SEND_UNCONFIRMED_MS);
    try {
      const accepted = await api.send(pending.request);
      clock.clearTimeout(timer);
      if (!store().beginRun(ticket, accepted.run_id, accepted.conversation_id)) return 'stale';
      watch(accepted.run_id);
      kick();
      return 'accepted';
    } catch (error) {
      clock.clearTimeout(timer);
      if (store().pending?.sendId !== ticket.sendId) return 'stale';
      return onSendFailure(ticket, error);
    }
  }

  async function createIfNeeded(draft: SendDraft): Promise<boolean> {
    if (!needsCreate(store())) return true;
    const ticket = store().beginCreate();
    if (!ticket) return false;
    const scope = store().scope;
    try {
      const created = await api.createConversation({
        scope_pitch_id: scope?.pitchId ?? null,
        page: draft.page,
        memory_off: true,
      });
      store().finishCreate(ticket, created.conversation_id);
      return store().conversationId === created.conversation_id;
    } catch (error) {
      store().finishCreate(ticket, null);
      if (error instanceof ApiFailure) {
        if (error.status === 503 && error.code === 'AGENT_DISABLED') emit({ type: 'disabled' });
        else emit({ type: 'sendFailed', code: error.code, status: error.status });
      } else emit({ type: 'sendFailed', code: 'REQUEST_FAILED', status: 0 });
      return false;
    }
  }

  // ── 바깥에 내는 동작 ─────────────────────────────────────────

  const offOnline = (options.onOnline ?? defaultOnOnline)(() => {
    // 연결이 돌아오면 바로 (L3-19)
    if (store().syncing) lookup().catch(() => undefined);
    if (store().run) kick();
  });

  return {
    send: async (draft) => {
      if (!(await createIfNeeded(draft))) return needsCreate(store()) ? 'failed' : 'locked';
      const ticket = store().beginSend(draft);
      if (!ticket) return 'locked';
      return post(ticket);
    },

    retryUnconfirmed: async () => {
      const ticket = pendingSendTicket();
      if (!ticket) return 'none';
      return post(ticket);
    },

    resume: () => {
      const s = store();
      if (s.syncing && s.conversationId) lookup().catch(() => undefined);
      else if (s.run) {
        watch(s.run.runId);
        kick();
      }
      // 되살린 보내기는 자동으로 다시 보내지 않습니다 — 미확정으로 알려 사용자가 다시 확인하게 합니다
      if (s.pending?.unconfirmed) emit({ type: 'unconfirmed', sendId: s.pending.sendId });
    },

    cancel: async () => {
      const run = store().run;
      if (!run || !store().beginCancel(run.runId)) return false;
      try {
        const view: unknown = await api.cancel(run.runId);
        if (isRunView(view) && isTerminalRun(view.status))
          finishRun(run.runId, run.conversationId, view);
        else kick();
      } catch {
        // 취소 요청이 실패해도 취소된 것으로 보지 않습니다 — 상태부터 다시 확인하고, 다시 취소할 수 있게 둡니다(L3-34)
        store().abortCancel(run.runId);
        kick();
      }
      return true;
    },

    changeScope: async (scope) => {
      if (store().conversationId === null) return store().chooseScope(scope);
      const ticket = store().beginScopeChange(scope);
      if (!ticket) return false;
      try {
        const changed = await api.changeScope(ticket.conversationId!, scope.pitchId);
        store().finishScopeChange(ticket, {
          pitchId: changed.scope_pitch_id,
          title: changed.scope_title,
        });
        return true;
      } catch (error) {
        store().finishScopeChange(ticket, null);
        emit({
          type: 'scopeFailed',
          code: error instanceof ApiFailure ? error.code : 'REQUEST_FAILED',
        });
        return false;
      }
    },

    checkNow: () => {
      const run = store().run;
      if (run) {
        // 직접 확인하면 늦어짐 표시를 처음부터 다시 셉니다
        startedAt.set(run.runId, clock.now());
        slowNotified.delete(run.runId);
        kick();
      } else if (store().syncing) lookup().catch(() => undefined);
    },

    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    dispose: () => {
      clearPoll();
      if (lookupTimer !== null) clock.clearTimeout(lookupTimer);
      lookupTimer = null;
      offOnline();
      listeners.clear();
    },
  };
}
