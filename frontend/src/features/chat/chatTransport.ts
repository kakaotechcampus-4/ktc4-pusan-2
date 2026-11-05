import { ResponseTooLargeError } from '@/shared/api/client';
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
      /** 아직 진행이 없으면 null (BE) */
      plan: PlanStep[] | null;
      trace: TraceItem[] | null;
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
  /**
   * 조회가 잇따라 실패 — "진행 상태를 확인하지 못해 다시 연결하고 있어요."
   * 응답이 2 MiB 를 넘었으면(`TOO_LARGE`) "일부 진행 정보를 표시하지 못했어요." (L3-22)
   */
  | { type: 'pollTrouble'; failures: number; reason: PollTroubleReason }
  | { type: 'pollRecovered' }
  /** 130초가 지나도 끝나지 않음 — "결과 확인이 늦어지고 있으니 상태를 다시 확인해 주세요." */
  | { type: 'slow'; runId: string }
  /** 실행 기록을 찾을 수 없음(404) — 메시지 목록을 다시 읽어 복구합니다 */
  | { type: 'lost'; runId: string }
  /** 대화를 찾을 수 없음 — "대화를 찾을 수 없어요." */
  | { type: 'conversationMissing'; conversationId: string }
  /** 범위 바꾸기 실패 — 칩은 앞 범위로 돌아갔습니다 */
  | { type: 'scopeFailed'; code: string };

/** 조회 실패 이유 — 형식 오류 · 크기 초과도 실패로 세어 간격을 늘립니다(L3-19 · L3-22) */
export type PollTroubleReason = 'TOO_LARGE' | 'FORMAT' | 'FAILED';

/** 응답 모양이 틀림 */
class FormatError extends Error {}

const troubleOf = (error: unknown): PollTroubleReason => {
  if (error instanceof ResponseTooLargeError) return 'TOO_LARGE';
  if (error instanceof FormatError || error instanceof SyntaxError) return 'FORMAT';
  return 'FAILED';
};

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
/** 곧바로 다시 읽기를 잇달아 몇 번까지 — 그 뒤에는 보통 간격으로 (FE 가 정함) */
export const MAX_REREADS = 3;

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

const isListOrNull = (v: unknown) => v === null || Array.isArray(v);
const isListOrMissing = (v: unknown) => v === undefined || Array.isArray(v);

/**
 * 실행 조회 응답을 읽습니다 — 최소 형식이 틀리면 null(실패로 봅니다, L3-19). 세부 검사는 응답 검사(4단계)가 맡습니다.
 *
 * BE #933 은 아직 `events` · `next_after_seq` · `cards` 를 싣지 않고(이벤트 커서 · 승인 API 가 남은 일),
 * `plan` · `trace` 는 진행이 없으면 null 입니다. 빠진 칸은 "새 줄 없음" 으로 채워, 끝날 때 메시지 목록을 다시 읽게 합니다.
 * 칸이 있는데 모양이 틀리면 형식 오류입니다
 */
function readRunView(v: unknown): RunView | null {
  if (typeof v !== 'object' || v === null) return null;
  const r = v as Partial<Record<keyof RunView, unknown>>;
  const ok =
    typeof r.run_id === 'string' &&
    typeof r.conversation_id === 'string' &&
    typeof r.status === 'string' &&
    isListOrNull(r.plan) &&
    isListOrNull(r.trace) &&
    isListOrMissing(r.events) &&
    (r.events ?? []).every(
      (e) => typeof e === 'object' && e !== null && Number.isInteger(e.event_seq),
    ) &&
    isListOrMissing(r.cards) &&
    (r.next_after_seq === undefined ||
      r.next_after_seq === null ||
      Number.isInteger(r.next_after_seq));
  if (!ok) return null;
  const view = v as RunView;
  return {
    ...view,
    events: view.events ?? [],
    next_after_seq: view.next_after_seq ?? null,
    cards: view.cards ?? [],
    error: view.error ?? null,
    retry_after_s: view.retry_after_s ?? null,
  };
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

  /**
   * 닫힌 뒤에는 고리를 다시 살리지 않습니다 — 진행 중이던 요청의 응답이 와도 예약 · 신호를 내지 않습니다.
   * 잠금을 푸는 저장소 갱신(접수 · 대화 만들기 · 범위 바꾸기 응답)은 표가 맞으면 그대로 반영합니다 — 새 통신 계층이
   * 저장소 변화를 구독해 이어 갑니다
   */
  let disposed = false;

  // ── 조회 고리 ───────────────────────────────────────────────
  let pollTimer: unknown = null;
  let polling = false;
  let pollAgain = false;
  let failures = 0;
  let idleDelay = 0;
  /** 곧바로 다시 읽기를 잇달아 한 횟수 — 건너뛴 순번이 메워지지 않거나 다음 커서가 끝없이 와도 멈추지 않게 */
  let rereads = 0;
  /** 마지막으로 알린 계획 · 진행 기록 — 이벤트가 없어도(BE 커서 미구현) 진행 패널이 바뀌게 */
  let lastShape: string | null = null;
  /** 실행마다 잰 시간. 승인 대기 시간은 뺍니다(12-3 "대기한 시간은 실행의 120초에서 뺀다") */
  const runClocks = new Map<
    string,
    { started: number; waited: number; waitingSince: number | null }
  >();
  const slowNotified = new Set<string>();

  // 대화 상태 조회도 한 번에 하나
  let lookupTimer: unknown = null;
  let lookingUp = false;
  let lookupAgain = false;
  let lookupFailures = 0;

  /** 15초 미확정 타이머 */
  let sendTimer: unknown = null;

  const clearTimer = (handle: unknown) => {
    if (handle !== null) clock.clearTimeout(handle);
  };

  const clearPoll = () => {
    clearTimer(pollTimer);
    pollTimer = null;
  };

  const schedulePoll = (ms: number) => {
    clearPoll();
    if (disposed) return;
    pollTimer = clock.setTimeout(() => {
      pollTimer = null;
      pollOnce().catch(() => undefined);
    }, ms);
  };

  /** 지금 바로 조회 — 조회 중이면 끝난 뒤 한 번 더 */
  const kick = () => {
    if (disposed) return;
    clearPoll();
    if (polling) pollAgain = true;
    else pollOnce().catch(() => undefined);
  };

  /** 실행 시간 기록을 만듭니다 — 이미 있으면 그대로 */
  const ensureClock = (runId: string) => {
    if (!runClocks.has(runId))
      runClocks.set(runId, { started: clock.now(), waited: 0, waitingSince: null });
    return runClocks.get(runId)!;
  };

  /** 새로 지켜보는 실행 — 간격 · 다시 읽기 횟수를 처음부터 */
  const watch = (runId: string) => {
    ensureClock(runId);
    idleDelay = 0;
    rereads = 0;
    lastShape = null;
  };

  const forget = (runId: string) => {
    runClocks.delete(runId);
    slowNotified.delete(runId);
  };

  /** 승인 대기를 뺀 실행 시간 — 이번 상태를 반영해 셉니다 */
  const elapsed = (runId: string, status: RunStatus) => {
    // 조회마다 불리므로 watch 를 부르지 않습니다 — 간격 · 다시 읽기 횟수를 되돌리면 늘림 · 제한이 풀립니다
    const c = ensureClock(runId);
    const now = clock.now();
    if (status === 'WAITING_APPROVAL') {
      if (c.waitingSince === null) c.waitingSince = now;
    } else if (c.waitingSince !== null) {
      c.waited += now - c.waitingSince;
      c.waitingSince = null;
    }
    const waitingNow = c.waitingSince === null ? 0 : now - c.waitingSince;
    return now - c.started - c.waited - waitingNow;
  };

  const failureDelay = (count: number) => {
    const base = FAILURE_STEPS_MS[Math.min(count, FAILURE_STEPS_MS.length) - 1]!;
    return Math.round(base * (0.8 + clock.random() * 0.4));
  };

  const nextDelay = (runId: string, status: RunStatus) => {
    if (elapsed(runId, status) >= SLOW_AFTER_MS) {
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

  /** 조회가 다시 됨 — 실패 안내를 거둡니다 */
  const recovered = () => {
    if (failures === 0 && lookupFailures === 0) return;
    failures = 0;
    lookupFailures = 0;
    emit({ type: 'pollRecovered' });
  };

  /** 끝남 — 지켜보는 실행일 때만 끝남을 알립니다. 이미 끝나 놓은 실행이면 그 실행의 취소 중 표시만 지웁니다 */
  const finishRun = (runId: string, conversationId: string, view: RunView) => {
    forget(runId);
    if (store().run?.runId !== runId) {
      store().endRun(runId);
      return;
    }
    store().endRun(runId, view.retry_after_s);
    idleDelay = 0;
    rereads = 0;
    recovered();
    emit({
      type: 'ended',
      runId,
      conversationId,
      status: view.status,
      errorCode: view.error?.code ?? null,
    });
  };

  async function pollOnce(): Promise<void> {
    if (disposed) return;
    const run = store().run;
    if (!run || polling) return;
    polling = true;
    pollAgain = false;
    const ticket = chatTicket();
    let view: RunView;
    try {
      const response = readRunView(await api.run(run.runId, run.afterSeq));
      if (!response || response.run_id !== run.runId) throw new FormatError();
      view = response;
    } catch (error) {
      polling = false;
      if (disposed) return;
      // 실패도 늦은 응답이면 버리고 지금 실행을 조회합니다 — 지난 실행의 404 로 새 실행의 고리가 멈추지 않게
      if (!isCurrent(ticket) || pollAgain) return kick();
      onPollFailure(run.runId, error);
      return;
    }
    polling = false;
    if (disposed) return;
    // 그사이 실행 · 대화 · 세대가 바뀌었으면 이 응답은 버리고 지금 상태로 다시 (L3-20)
    if (!isCurrent(ticket) || pollAgain) return kick();
    recovered();

    const { taken, gap } = takeEvents(run.afterSeq, view.events);
    const last = taken.at(-1);
    if (last) {
      store().advanceCursor(run.runId, last.event_seq);
      rereads = 0;
    }
    const shape = JSON.stringify([view.plan, view.trace]);
    const shapeChanged = shape !== lastShape && (view.plan !== null || view.trace !== null);
    lastShape = shape;
    if (taken.length > 0 || view.cards.length > 0 || shapeChanged)
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
    // 건너뛴 순번 · 나눠 보낸 나머지는 같은 커서로 곧바로 다시 읽습니다(L3-46) — 잇달아 몇 번까지만
    const more = gap || view.next_after_seq !== null;
    if (more && rereads < MAX_REREADS) {
      rereads += 1;
      return schedulePoll(0);
    }
    // 끝났으면 끝냅니다 — 끝까지 메워지지 않은 순번은 메시지 목록이 보여 줍니다
    if (isTerminalRun(view.status)) return finishRun(run.runId, run.conversationId, view);
    // 모르는 상태는 "확인 중" — 끝나지 않은 것으로 보고 계속 조회합니다(L3-22)
    if (KNOWN.includes(view.status)) store().setRunPhase(run.runId, asPhase(view.status));
    schedulePoll(nextDelay(run.runId, view.status));
  }

  function onPollFailure(runId: string, error: unknown) {
    if (error instanceof ApiFailure && error.status === 404) {
      // 실행 기록이 없어짐 — 놓고 목록에서 복구합니다(L3-21)
      store().endRun(runId);
      forget(runId);
      recovered();
      emit({ type: 'lost', runId });
      return;
    }
    failures += 1;
    emit({ type: 'pollTrouble', failures, reason: troubleOf(error) });
    schedulePoll(failureDelay(failures));
  }

  // ── 대화 상태 조회 (고르기 · 되살리기 · 409) ───────────────────

  const scheduleLookup = (ms: number) => {
    clearTimer(lookupTimer);
    lookupTimer = null;
    if (disposed) return;
    lookupTimer = clock.setTimeout(() => {
      lookupTimer = null;
      lookup().catch(() => undefined);
    }, ms);
  };

  /**
   * 대화의 실행을 조회합니다. 조회 중에 또 불리면(예: 대화를 바로 바꿈) 끝난 뒤 지금 대화로 한 번 더 —
   * 앞 대화의 늦은 응답은 버리고, 새 대화가 "상태 확인 중" 에 갇히지 않게 합니다
   */
  async function lookup(): Promise<void> {
    if (disposed) return;
    const s = store();
    if (!s.syncing || !s.conversationId) return;
    if (lookingUp) {
      lookupAgain = true;
      return;
    }
    lookingUp = true;
    lookupAgain = false;
    clearTimer(lookupTimer);
    lookupTimer = null;
    const ticket = chatTicket();
    const conversationId = s.conversationId;
    const before = s.run?.runId ?? null;
    try {
      // BE 는 이 대화의 마지막 실행을 끝났어도 돌려주고, 한 번도 없으면 null 입니다
      const response = await api.conversationRun(conversationId);
      const run = response === null ? null : readRunView(response);
      if (response !== null && (!run || run.conversation_id !== conversationId))
        throw new FormatError();
      lookingUp = false;
      if (disposed) return;
      const found =
        run && !isTerminalRun(run.status)
          ? { runId: run.run_id, phase: asPhase(run.status) }
          : null;
      const applied = store().resolveConversation(ticket, found);
      if (applied) {
        recovered();
        // 새 실행이면 저장소 구독이 조회를 시작합니다. 되살린 그 실행이면 여기서 이어 갑니다
        if (found && found.runId === before) {
          watch(found.runId);
          kick();
        }
      }
      if (lookupAgain || (!applied && store().syncing)) return lookup();
    } catch (error) {
      lookingUp = false;
      if (disposed) return;
      if (lookupAgain || !isCurrent(ticket)) return lookup();
      if (error instanceof ApiFailure && error.status === 404) {
        store().resolveConversation(ticket, null);
        emit({ type: 'conversationMissing', conversationId });
        return;
      }
      lookupFailures += 1;
      emit({ type: 'pollTrouble', failures: lookupFailures, reason: troubleOf(error) });
      scheduleLookup(failureDelay(lookupFailures));
    }
  }

  /** 조회 중이 아니면 조회 — 같은 상태를 여러 곳에서 깨워도 겹쳐 부르지 않습니다 */
  const ensureLookup = () => {
    if (!lookingUp) lookup().catch(() => undefined);
  };

  // ── 보내기 ─────────────────────────────────────────────────

  const clearSendTimer = () => {
    clearTimer(sendTimer);
    sendTimer = null;
  };

  /** 15초 안에 접수가 확정되지 않으면 미확정으로 알립니다 — 다시 보내지는 않습니다 */
  const armUnconfirmed = (ticket: ChatTicket) => {
    clearSendTimer();
    if (disposed) return;
    sendTimer = clock.setTimeout(() => {
      sendTimer = null;
      if (store().pending?.sendId !== ticket.sendId) return;
      store().markUnconfirmed(ticket);
      emit({ type: 'unconfirmed', sendId: ticket.sendId! });
    }, SEND_UNCONFIRMED_MS);
  };

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
      // 다른 탭이 실행 중 — 저장소가 "상태 확인 중" 이 되면 구독이 조회를 시작합니다(L3-17)
      store().conflictSend(ticket);
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
    if (disposed || !pending || pending.sendId !== ticket.sendId) return 'stale';
    armUnconfirmed(ticket);
    try {
      const accepted = await api.send(pending.request);
      clearSendTimer();
      // 접수는 닫힌 뒤에도 반영합니다 — 잠금을 풀고, 새 통신 계층이 저장소 구독으로 조회를 시작합니다
      if (!store().beginRun(ticket, accepted.run_id, accepted.conversation_id)) return 'stale';
      return 'accepted';
    } catch (error) {
      clearSendTimer();
      // 닫혔으면 결과를 모르는 채로 둡니다 — 새 통신 계층이 미확정으로 알립니다
      if (disposed || store().pending?.sendId !== ticket.sendId) return 'stale';
      return onSendFailure(ticket, error);
    }
  }

  async function createIfNeeded(): Promise<boolean> {
    if (!needsCreate(store())) return true;
    const ticket = store().beginCreate();
    if (!ticket) return false;
    const scope = store().scope;
    try {
      // (BE 미구현) `memory_off` — 지금 BE 는 모르는 칸이라 422 로 거절합니다. 목은 받습니다
      const created = await api.createConversation({
        pitch_id: scope?.pitchId ?? null,
        memory_off: true,
      });
      store().finishCreate(ticket, created.conversation_id);
      return !disposed && store().conversationId === created.conversation_id;
    } catch (error) {
      store().finishCreate(ticket, null);
      if (error instanceof ApiFailure) {
        if (error.status === 503 && error.code === 'AGENT_DISABLED') emit({ type: 'disabled' });
        else emit({ type: 'sendFailed', code: error.code, status: error.status });
      } else emit({ type: 'sendFailed', code: 'REQUEST_FAILED', status: 0 });
      return false;
    }
  }

  // ── 저장소 · 연결 구독 ───────────────────────────────────────

  /**
   * 저장소가 바뀌면 이어 갑니다 — 새 실행이 붙으면 조회를, "상태 확인 중" 이 켜지면 대화의 실행 조회를 시작합니다.
   * 접수 · 409 · 대화 고르기 · 다른(닫힌) 통신 계층의 반영까지 한 길로 받습니다
   */
  const offStore = useChatStore.subscribe((now, before) => {
    if (disposed) return;
    if (now.run && now.run.runId !== before.run?.runId) {
      watch(now.run.runId);
      kick();
    }
    if (now.syncing && !before.syncing && now.conversationId) lookup().catch(() => undefined);
  });

  const offOnline = (options.onOnline ?? defaultOnOnline)(() => {
    // 연결이 돌아오면 바로 (L3-19)
    if (store().syncing) ensureLookup();
    if (store().run) kick();
  });

  // ── 바깥에 내는 동작 ─────────────────────────────────────────

  return {
    send: async (draft) => {
      if (disposed) return 'stale';
      if (!(await createIfNeeded())) return needsCreate(store()) ? 'failed' : 'locked';
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
      if (disposed) return;
      const s = store();
      if (s.syncing && s.conversationId) ensureLookup();
      else if (s.run) {
        watch(s.run.runId);
        kick();
      }
      if (s.pending) {
        // 되살린 보내기는 자동으로 다시 보내지 않습니다 — 미확정으로 알려 사용자가 다시 확인하게 합니다.
        // 아직 결과를 기다리던 보내기(앞 통신 계층이 보낸 것)는 여기서 15초를 다시 셉니다
        if (s.pending.unconfirmed) emit({ type: 'unconfirmed', sendId: s.pending.sendId });
        else armUnconfirmed(pendingSendTicket()!);
      }
    },

    cancel: async () => {
      if (disposed) return false;
      const run = store().run;
      if (!run || !store().beginCancel(run.runId)) return false;
      try {
        const view = readRunView(await api.cancel(run.runId));
        if (disposed) return true;
        // 끝남은 그 실행을 아직 지켜볼 때만 알립니다 — 조회가 먼저 끝냈으면 취소 중 표시만 지웁니다
        if (view && view.run_id === run.runId && isTerminalRun(view.status))
          finishRun(run.runId, run.conversationId, view);
        else if (store().run?.runId === run.runId) kick();
      } catch {
        if (disposed) return true;
        // 취소 요청이 실패해도 취소된 것으로 보지 않습니다 — 상태부터 다시 확인하고, 다시 취소할 수 있게 둡니다(L3-34)
        store().abortCancel(run.runId);
        if (store().run?.runId === run.runId) kick();
      }
      return true;
    },

    changeScope: async (scope) => {
      if (disposed) return false;
      if (store().conversationId === null) return store().chooseScope(scope);
      const ticket = store().beginScopeChange(scope);
      if (!ticket) return false;
      try {
        const changed = await api.changeScope(ticket.conversationId!, scope.pitchId);
        store().finishScopeChange(ticket, {
          pitchId: changed.scope_pitch_id,
          title: changed.scope_pitch_title,
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
      if (disposed) return;
      const run = store().run;
      if (run) {
        // 직접 확인하면 늦어짐을 처음부터 다시 셉니다
        forget(run.runId);
        watch(run.runId);
        kick();
      } else if (store().syncing) ensureLookup();
    },

    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    dispose: () => {
      disposed = true;
      clearPoll();
      clearTimer(lookupTimer);
      lookupTimer = null;
      clearSendTimer();
      offStore();
      offOnline();
      listeners.clear();
    },
  };
}
