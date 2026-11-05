import { ResponseTooLargeError } from '@/shared/api/client';
import { ApiFailure } from '@/shared/api/tokenStore';
import { isTerminalRun } from '@/shared/lib/chatAgent';
import type {
  ActionDecisionRequest,
  PlanStep,
  RunEvent,
  RunStatus,
  RunView,
  TraceItem,
} from '@/types/chat';
import { chatApi, type ChatApi } from './chatApi';
import {
  createRunReader,
  readPlan,
  readTrace,
  type ContractError,
  type RunReader,
  type ShownCard,
  type ShownClaim,
} from './chatResponse';
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
  /**
   * 새로 받은 진행 줄 · 문장 줄과 지금 실행 모습 — 모두 응답 검사(`chatResponse`)를 거친 값입니다.
   * 화면은 `claims` 를 차례로 그리고, `contractError` 가 있으면 그 안내를 붙입니다
   */
  | {
      type: 'progress';
      runId: string;
      conversationId: string;
      status: RunStatus;
      /** 받은 줄 그대로(순번순) — 검사 전 값이라 그리지 않습니다 */
      events: RunEvent[];
      /** 새로 그릴 문장 — 같은 문장은 한 번만, 계약 오류 뒤의 문장은 빠집니다 */
      claims: ShownClaim[];
      /**
       * 이 실행의 문장을 처음부터 다시 보냄 — 화면은 이 실행에 그려 둔 문장을 비우고 `claims` 로 다시 그립니다.
       * 다른 대화로 갔다 돌아오거나 통신 계층이 새로 생겼을 때 첫 진행 신호에만 true 입니다
       */
      restart: boolean;
      /**
       * 이미 만들어진 문장을 따라잡는 묶음 — 새로고침 · 다시 열기 · 대화를 다시 고름 · 다른 탭이 시작한 실행.
       * 화면은 글자 효과 없이 그립니다(12-4 F12 "새로고침 · 다시 열기 · 지난 메시지는 효과 없이"). 이 화면에서 보낸
       * 실행을 처음 받을 때는 false 라 효과로 보입니다
       */
      catchUp: boolean;
      /** 이 실행에서 난 계약 오류. 한 번 나면 끝날 때까지 그대로 */
      contractError: ContractError | null;
      /** 아직 진행이 없으면 null (BE). 깨진 항목은 빠집니다 */
      plan: PlanStep[] | null;
      trace: TraceItem[] | null;
      /** 깨진 진행 항목을 뺐음 — "일부 진행 정보를 표시하지 못했어요." */
      progressDropped: boolean;
      /** 승인 대기 중 카드 — 모르는 카드는 대체 표시, 계약 오류 뒤에는 행동 카드가 빠집니다 */
      cards: ShownCard[];
    }
  /** 실행이 끝남 — 화면은 메시지 목록을 다시 읽습니다 */
  | {
      type: 'ended';
      runId: string;
      conversationId: string;
      status: RunStatus;
      errorCode: string | null;
      /**
       * 승인 대기(`WAITING_APPROVAL`)에서 곧바로 끝남(만료 · 취소 · 새 메시지). 이때는 BE 가 저장한 고정 문장
       * ("확인 시간이 지나 …" 등)만 보이고 '실행 만료 · 취소' 안내를 함께 보이지 않습니다(12-4 F8)
       */
      endedWhileWaiting: boolean;
    }
  /** 15초 넘게 접수를 확인하지 못함 — "전송 여부를 확인하지 못했으니 다시 확인해 주세요." */
  | { type: 'unconfirmed'; sendId: string }
  /** 보내기 · 대화 만들기가 거절됨 — 코드별 안내(F8) */
  | { type: 'sendFailed'; code: string; status: number }
  /**
   * 꺼짐 — 버튼을 숨깁니다. 보내기 · 실행 조회 · 대화 조회 · 취소 · 승인 · 범위 바꾸기 어디서 503 `AGENT_DISABLED` 를
   * 받아도 냅니다. 조회는 멈추고, 다시 켜지면 화면이 `resume()` 으로 이어 갑니다
   */
  | { type: 'disabled' }
  /**
   * 조회가 잇따라 실패 — "진행 상태를 확인하지 못해 다시 연결하고 있어요."
   * 응답이 2 MiB 를 넘었으면(`TOO_LARGE`) "일부 진행 정보를 표시하지 못했어요." (L3-22)
   */
  | { type: 'pollTrouble'; failures: number; reason: PollTroubleReason }
  /** 로그인이 풀렸거나 다른 계정으로 바뀜 — 조회를 멈춥니다. "로그인이 만료됐으니 다시 로그인해 주세요." (L3-04) */
  | { type: 'sessionExpired' }
  /** 취소하지 못함 — 실행은 그대로이고 다시 취소할 수 있습니다(L3-34) */
  | { type: 'cancelFailed'; runId: string }
  /**
   * 취소 응답을 잃음 — 취소됐는지 상태 조회로 확인하는 중입니다. "취소 여부를 확인하고 있어요." (L3-34).
   * 그동안 "취소 중" 잠금은 그대로이고, 다음 조회가 끝남(취소됨)이나 cancelFailed 로 확정합니다
   */
  | { type: 'cancelChecking'; runId: string }
  /**
   * 승인 · 거절이 받아들여지지 않음 — 카드가 바뀌었거나(`AGENT_STATE_INVALID`) 이미 만료 · 취소됐거나
   * (`AGENT_EXPIRED` · `AGENT_CANCELLED`) 결과를 모릅니다. 상태를 다시 읽어 카드를 새로 보입니다(L3-39 · L3-40).
   * 결과를 모르는 경우는 같은 요청을 다시 보내면 처음 결과를 받습니다
   */
  | { type: 'decisionFailed'; runId: string; actionId: string; code: string }
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
export type PollTroubleReason = 'TOO_LARGE' | 'FORMAT' | 'TIMEOUT' | 'FAILED';

/** 응답 모양이 틀림 */
class FormatError extends Error {}

/** 정한 시간 안에 응답이 오지 않음 — 걸린 요청 하나가 조회 · 잠금을 영영 붙잡지 않게 합니다(L3-19) */
class RequestTimeout extends Error {}

const troubleOf = (error: unknown): PollTroubleReason => {
  if (error instanceof ResponseTooLargeError) return 'TOO_LARGE';
  if (error instanceof FormatError || error instanceof SyntaxError) return 'FORMAT';
  if (error instanceof RequestTimeout) return 'TIMEOUT';
  return 'FAILED';
};

/**
 * 승인 · 거절 실패의 코드 — 바뀐 카드(해시 불일치)는 문서 이름 `APPROVAL_HASH_MISMATCH` 로 맞춥니다.
 * 지금 BE 는 이 409 에 `AGENT_STATE_INVALID` 를 주는데, 그 코드는 화면에서 "실행 상태가 만료되었거나 …"(실행 오류)
 * 문구라 카드에는 맞지 않습니다. 승인 응답에서 온 409 만 바꿉니다(12-4 F8 · 5-9)
 */
const decisionCode = (error: unknown) => {
  if (!(error instanceof ApiFailure)) return 'REQUEST_FAILED';
  if (error.status === 409 && error.code === 'AGENT_STATE_INVALID') return 'APPROVAL_HASH_MISMATCH';
  return error.code;
};

const isSignedOut = (error: unknown) => error instanceof ApiFailure && error.status === 401;
/** 대화 스위치가 꺼짐 — BE 는 실행 조회 · 취소 · 승인에도 이 503 을 줍니다(`Enabled` 의존성) */
const isDisabled = (error: unknown) =>
  error instanceof ApiFailure && error.status === 503 && error.code === 'AGENT_DISABLED';
/** 결과를 모름 — 끊김 · 시간 초과 · 꺼짐이 아닌 5xx. 서버가 처리했을 수 있습니다 */
const isUnknownResult = (error: unknown) =>
  !(error instanceof ApiFailure) || (error.status >= 500 && !isDisabled(error));

/** 승인 · 거절 결과 — 'locked' 는 이미 보내는 중이거나 승인 대기가 아님 */
export type DecideResult = 'decided' | 'locked' | 'failed' | 'stale';

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
  /** [승인] · [거절] — 화면에 보인 카드의 `args_hash` 만 보냅니다. 같은 카드는 한 번에 하나만 */
  decide: (
    actionId: string,
    decision: ActionDecisionRequest['decision'],
    argsHash: string,
  ) => Promise<DecideResult>;
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
/**
 * 조회 · 취소 · 대화 만들기 · 범위 바꾸기의 응답을 기다리는 한도 — 넘으면 실패로 세고 간격을 늘려 다시 합니다.
 * 보내기는 15초 미확정이 따로 있어 여기에 넣지 않습니다(자동으로 다시 보내지 않으므로)
 */
export const REQUEST_TIMEOUT_MS = 20_000;
/**
 * 대화의 실행을 찾을 때 보내는 커서 — 끝 너머라 이벤트 없이 상태만 받습니다. 이벤트는 그 실행을 지켜보며
 * 자기 커서로 받습니다(BE 는 `after_seq` 기본값 0 이라 빼면 이벤트를 다 실어 보냅니다)
 */
export const STATUS_ONLY_AFTER_SEQ = 2_147_483_647;
export const POLL_RUNNING_MS = 1_000;
/**
 * 그 밖의 조회 — 접수 대기는 2초 고정, 승인 대기만 2초에서 1.5배씩 15초까지. 문서 #900 은 "2초 · 늘림",
 * #948 은 "2초마다" 라 조율 중입니다(BE `get_run` 주석은 "늘려 가며")
 */
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
 * 실행 조회 응답을 읽습니다 — 최소 형식이 틀리면 null(실패로 봅니다, L3-19). 세부 검사는 응답 검사(`chatResponse`)가 맡습니다.
 *
 * `plan` · `trace` 는 진행이 없으면 null 입니다. 취소 · 승인 · 거절의 응답은 이벤트가 없고 `next_after_seq` 가 null 입니다.
 * 빠진 칸(예전 BE)은 "새 줄 없음" 으로 채우고, 칸이 있는데 모양이 틀리면 형식 오류입니다.
 * 이벤트는 내용이 있거나, 가렸으면(`masked`) 내용이 null 이어야 합니다
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
      (e) =>
        typeof e === 'object' &&
        e !== null &&
        Number.isInteger(e.event_seq) &&
        typeof e.event === 'string' &&
        (e.masked === true
          ? e.data === null
          : typeof e.data === 'object' && e.data !== null && (e.masked ?? false) === false),
    ) &&
    (r.has_more === undefined || typeof r.has_more === 'boolean') &&
    isListOrMissing(r.cards) &&
    (r.next_after_seq === undefined ||
      r.next_after_seq === null ||
      Number.isInteger(r.next_after_seq));
  if (!ok) return null;
  const view = v as RunView;
  return {
    ...view,
    events: (view.events ?? []).map((e) => ({ ...e, masked: e.masked === true }) as RunEvent),
    next_after_seq: view.next_after_seq ?? null,
    has_more: view.has_more === true,
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
  /**
   * 실행마다 문장 읽기 — 커서와 수명을 같이 합니다. 읽기가 없는 실행을 지켜보기 시작하면(새 통신 계층 ·
   * 다시 연 화면) 커서를 처음으로 되감아, 앞 문장부터 다시 받고 순번이 0부터 이어지게 합니다
   */
  const readers = new Map<string, { reader: RunReader; fresh: boolean; catchUp: boolean }>();
  /**
   * 새 읽기가 따라잡기로 시작하는지 — 전에 문장을 보여 준 실행이거나(다시 열기 · 대화를 다시 고름) 이 화면에서
   * 보내지 않은 실행이면(새로고침 · 다른 탭) 이미 만들어진 문장부터 받습니다
   */
  const newReader = (runId: string) => ({
    reader: createRunReader(),
    fresh: true,
    catchUp: everShown.has(runId) || !startedHere.has(runId),
  });
  /** 이 통신 계층이 보내 시작한 실행 · 문장을 한 번이라도 보낸 실행 — 글자 효과를 줄지(`catchUp`) 정합니다 */
  const startedHere = new Set<string>();
  /** 이 통신 계층이 끝낸 실행 — 늦게 온 대화 조회가 다시 붙이지 않게(`forget` 이 지우지 않습니다) */
  const finishedRuns = new Set<string>();
  const everShown = new Set<string>();
  /** 조회를 멈춘 이유 — 꺼짐 · 로그인 만료. 연결이 돌아와도 다시 두드리지 않습니다 */
  let halted: 'disabled' | 'session' | null = null;
  /** 응답을 잃은 취소의 실행 — 다음 조회가 확정합니다 */
  let cancelUnsure: string | null = null;
  /** 보내는 중인 승인 · 거절 (L3-39 "`action_id` 별로 막는다") */
  const deciding = new Set<string>();
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

  /** 끊을 수 있는 조회 — 닫을 때 · 대화를 바꿀 때 걸린 요청을 끊습니다 */
  let pollController: AbortController | null = null;
  let lookupController: AbortController | null = null;

  /**
   * 응답을 정한 시간까지만 기다리고, 넘으면 요청을 끊습니다 — 걸린 요청이 연결을 쌓아 다른 API 까지 막지 않게.
   * 끊어도 서버는 이미 처리했을 수 있어, 결과를 모르는 경우는 부른 쪽이 상태를 다시 읽어 확정합니다
   */
  const timed = <T>(
    start: (signal: AbortSignal) => Promise<T>,
    hold?: (controller: AbortController) => void,
  ): Promise<T> => {
    const controller = new AbortController();
    hold?.(controller);
    return new Promise<T>((resolve, reject) => {
      const handle = clock.setTimeout(() => {
        reject(new RequestTimeout());
        controller.abort();
      }, REQUEST_TIMEOUT_MS);
      start(controller.signal).then(
        (value) => {
          clock.clearTimeout(handle);
          resolve(value);
        },
        (error: unknown) => {
          clock.clearTimeout(handle);
          reject(error);
        },
      );
    });
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
    if (!readers.has(runId)) {
      readers.set(runId, newReader(runId));
      store().rewindCursor(runId);
    }
  };

  const forget = (runId: string) => {
    runClocks.delete(runId);
    slowNotified.delete(runId);
    readers.delete(runId);
    startedHere.delete(runId);
    everShown.delete(runId);
    if (cancelUnsure === runId) cancelUnsure = null;
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
    // 접수 대기는 곧 실행으로 바뀌니 2초 그대로 — 늘리면 실행이 시작된 뒤 첫 문장이 최대 15초 늦게 보입니다
    if (status !== 'WAITING_APPROVAL') {
      idleDelay = 0;
      return POLL_IDLE_START_MS;
    }
    // 승인 대기는 사용자를 기다리는 동안이라(최대 30분) 2초에서 늘립니다 — 문서 #948 "2초마다" 와 조율 중
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
    finishedRuns.add(runId);
    forget(runId);
    if (store().run?.runId !== runId) {
      store().endRun(runId);
      return;
    }
    const endedWhileWaiting = store().run?.phase === 'WAITING_APPROVAL';
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
      endedWhileWaiting,
    });
    syncScope(conversationId);
  };

  /**
   * 실행이 끝나면 서버가 확정한 대화 범위로 칩을 맞춥니다 — 에이전트가 다른 피치를 열면(`FETCH_PITCH`) BE 가
   * 검증된 `final` 로 범위를 바꾸는데, 실행 조회에는 범위가 없어 대화를 다시 읽습니다(12-4 F2).
   * 그사이 대화 · 사용자가 바뀌었거나 사용자가 범위를 바꾸는 중이면 받지 않습니다(`applyServerScope`)
   */
  const syncScope = (conversationId: string) => {
    if (disposed) return;
    const ticket = chatTicket();
    if (ticket.conversationId !== conversationId) return;
    // 요청한 때의 범위 — 응답이 오기 전에 사용자가 칩을 바꿨으면(바꾸기가 이미 끝났어도) 늦은 목록으로 덮지 않습니다
    const asked = store().scope;
    serverScope(conversationId)
      .then((server) => {
        if (disposed || !server || store().scope !== asked) return;
        const now = store().scope;
        if (now?.pitchId === server.pitchId && now?.title === server.title) return;
        store().applyServerScope(ticket, server);
      })
      .catch(() => undefined);
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
      const response = readRunView(
        await timed(
          (signal) => api.run(run.runId, run.afterSeq, signal),
          (c) => (pollController = c),
        ),
      );
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
    // 지켜보기 전에 응답이 먼저 온 실행도 읽기를 갖게 합니다(되감기는 하지 않음 — 이미 처음부터 받는 중)
    let entry = readers.get(run.runId);
    if (!entry) {
      entry = newReader(run.runId);
      readers.set(run.runId, entry);
    }
    const { reader } = entry;
    const errorBefore = reader.error;
    const claims = reader.add(taken);
    const plan = readPlan(view.plan);
    const trace = readTrace(view.trace);
    const shape = JSON.stringify([view.plan, view.trace]);
    const shapeChanged = shape !== lastShape && (view.plan !== null || view.trace !== null);
    lastShape = shape;
    if (taken.length > 0 || view.cards.length > 0 || shapeChanged || reader.error !== errorBefore) {
      const restart = entry.fresh;
      entry.fresh = false;
      // 따라잡기는 이 읽기가 처음 문장을 받을 때까지 이어집니다 — 첫 응답이 할 일 목록뿐이어도 그 뒤의 옛 문장을
      // 효과로 다시 보이지 않게. 첫 문장 묶음 뒤로 오는 문장은 새로 만들어진 것이라 효과로 보입니다
      const catchUp = entry.catchUp;
      if (claims.length > 0) {
        entry.catchUp = false;
        everShown.add(run.runId);
      }
      emit({
        type: 'progress',
        runId: run.runId,
        conversationId: run.conversationId,
        status: view.status,
        events: taken,
        claims,
        restart,
        catchUp,
        contractError: reader.error,
        plan: view.plan === null ? null : plan.items,
        trace: view.trace === null ? null : trace.items,
        progressDropped: plan.dropped || trace.dropped,
        cards: reader.cards(view.cards),
      });
    }
    // 응답을 잃은 취소 — 끝나지 않은 상태가 보이면 취소되지 않은 것으로 확정합니다(L3-34)
    if (cancelUnsure === run.runId && !isTerminalRun(view.status)) {
      cancelUnsure = null;
      store().abortCancel(run.runId);
      emit({ type: 'cancelFailed', runId: run.runId });
    }
    // 건너뛴 순번 · 크기 때문에 나눠 보낸 나머지(`has_more`)는 같은 커서로 곧바로 다시 읽습니다(L3-46) —
    // 잇달아 몇 번까지만. `next_after_seq` 는 늘 "다음 커서" 라 남은 것이 있다는 뜻이 아닙니다
    const more = gap || view.has_more;
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
    // 로그인이 풀림 — 같은 토큰으로 계속 두드리지 않습니다. 다시 로그인하면 사용자 전환이 상태를 새로 엽니다
    if (isSignedOut(error)) {
      halted = 'session';
      emit({ type: 'sessionExpired' });
      return;
    }
    // 실행 중에 스위치가 꺼짐 — 다시 연결 중으로 계속 두드리지 않고 멈춥니다
    if (isDisabled(error)) {
      halted = 'disabled';
      emit({ type: 'disabled' });
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
      const response = await timed(
        (signal) => api.conversationRun(conversationId, STATUS_ONLY_AFTER_SEQ, signal),
        (c) => (lookupController = c),
      );
      const run = response === null ? null : readRunView(response);
      if (response !== null && (!run || run.conversation_id !== conversationId))
        throw new FormatError();
      lookingUp = false;
      if (disposed) return;
      // 이미 끝낸 실행을 서버가 아직 진행 중으로 보낸 늦은 모습이면 끝난 것으로 봅니다
      const found =
        run && !isTerminalRun(run.status) && !finishedRuns.has(run.run_id)
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
      if (isSignedOut(error)) {
        halted = 'session';
        emit({ type: 'sessionExpired' });
        return;
      }
      if (isDisabled(error)) {
        halted = 'disabled';
        emit({ type: 'disabled' });
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
    // 접수 중 DB 가 잠깐 안 됨 — BE 는 "FE 는 같은 client_request_id 로 다시 보낸다" 고 정했습니다.
    // 자동으로 다시 보내지 않고, 미확정으로 두어 사용자가 [다시 확인]을 누르면 같은 키로 보냅니다(L3-18)
    if (status === 503 && code === 'AGENT_NOT_ACCEPTED') return unconfirmed();
    // 그 밖의 503 은 커밋 전 실패(12-3 "커밋 전 실패는 503") — 접수되지 않았습니다. 그 밖 5xx 는 받았는지 모릅니다
    if (status >= 500 && status !== 503) return unconfirmed();
    store().failSend(ticket);
    emit({ type: 'sendFailed', code, status });
    return 'failed';
  }

  /**
   * 보내기 시도 번호와 그 요청 — [다시 확인]을 거듭 누르면 앞 시도를 끊습니다. 같은 `client_request_id` 라 끊어도
   * 안전하고(서버가 받았으면 다음 시도가 같은 202 를 받음), 걸린 POST 가 쌓여 연결을 막지 않습니다.
   * 앞 시도의 늦은 결과는 버려 뒤 시도의 15초 타이머를 지우지 않습니다
   */
  let sendAttempt = 0;
  let sendController: AbortController | null = null;

  async function post(ticket: ChatTicket): Promise<SendResult> {
    const pending = store().pending;
    if (disposed || !pending || pending.sendId !== ticket.sendId) return 'stale';
    const attempt = ++sendAttempt;
    sendController?.abort();
    const controller = new AbortController();
    sendController = controller;
    armUnconfirmed(ticket);
    try {
      const accepted = await api.send(pending.request, controller.signal);
      // 접수는 몇 번째 시도의 응답이든 반영합니다(같은 키라 같은 실행) — 타이머는 마지막 시도 것만 지웁니다
      if (attempt === sendAttempt) clearSendTimer();
      // 접수는 닫힌 뒤에도 반영합니다 — 잠금을 풀고, 새 통신 계층이 저장소 구독으로 조회를 시작합니다
      // 저장소에 붙이기 전에 표시합니다 — 붙이는 순간 구독이 조회를 시작합니다
      startedHere.add(accepted.run_id);
      if (!store().beginRun(ticket, accepted.run_id, accepted.conversation_id)) return 'stale';
      return 'accepted';
    } catch (error) {
      // 뒤 시도가 있으면 이 시도의 실패(끊김 포함)는 버립니다
      if (attempt !== sendAttempt) return 'stale';
      clearSendTimer();
      // 닫혔으면 결과를 모르는 채로 둡니다 — 새 통신 계층이 미확정으로 알립니다
      if (disposed || store().pending?.sendId !== ticket.sendId) return 'stale';
      return onSendFailure(ticket, error);
    } finally {
      if (sendController === controller) sendController = null;
    }
  }

  async function createIfNeeded(): Promise<boolean> {
    if (!needsCreate(store())) return true;
    const ticket = store().beginCreate();
    if (!ticket) return false;
    const scope = store().scope;
    try {
      // (BE 미구현) `memory_off` — 지금 BE 는 모르는 칸이라 422 로 거절합니다. 목은 받습니다
      // 결과를 모른 채 실패로 알리면 대화가 하나 더 생길 수 있습니다 — '기억 없이' 는 아직 BE 가 받지 않아(422)
      // 실제로는 생기지 않고, BE 가 칸을 열 때 멱등 키를 함께 맞춥니다
      const created = await timed((signal) =>
        api.createConversation({ pitch_id: scope?.pitchId ?? null, memory_off: true }, signal),
      );
      store().finishCreate(ticket, created.conversation_id);
      return !disposed && store().conversationId === created.conversation_id;
    } catch (error) {
      // 그사이 대화 · 사용자가 바뀌었으면 잠금만 풀고 안내하지 않습니다 — 새 화면에 엉뚱한 실패가 뜨지 않게
      const current = isCurrent(ticket);
      store().finishCreate(ticket, null);
      if (!current || disposed) return false;
      if (error instanceof ApiFailure) {
        if (error.status === 503 && error.code === 'AGENT_DISABLED') emit({ type: 'disabled' });
        else emit({ type: 'sendFailed', code: error.code, status: error.status });
      } else emit({ type: 'sendFailed', code: 'REQUEST_FAILED', status: 0 });
      return false;
    }
  }

  /** 대화 목록에서 그 대화의 범위 — 읽지 못하면 null */
  async function serverScope(conversationId: string): Promise<ChatScope | null> {
    try {
      const list = await timed((signal) => api.listConversations(signal));
      const items = (list as { conversations?: unknown } | null)?.conversations;
      const found = Array.isArray(items)
        ? (items as { conversation_id?: unknown }[]).find(
            (c) => c.conversation_id === conversationId,
          )
        : undefined;
      if (!found) return null;
      const { scope_pitch_id: pitchId, scope_pitch_title: title } = found as {
        scope_pitch_id?: unknown;
        scope_pitch_title?: unknown;
      };
      if (!(pitchId === null || typeof pitchId === 'string')) return null;
      return { pitchId, title: typeof title === 'string' ? title : null };
    } catch {
      return null;
    }
  }

  // ── 저장소 · 연결 구독 ───────────────────────────────────────

  /**
   * 저장소가 바뀌면 이어 갑니다 — 새 실행이 붙으면 조회를, "상태 확인 중" 이 켜지면 대화의 실행 조회를 시작합니다.
   * 접수 · 409 · 대화 고르기 · 다른(닫힌) 통신 계층의 반영까지 한 길로 받습니다
   */
  const offStore = useChatStore.subscribe((now, before) => {
    if (disposed) return;
    // 지켜보던 실행을 떠나면(다른 대화 · 로그아웃) 그 읽기를 버립니다 — 돌아오면 처음부터 다시 받습니다(restart)
    const runChanged = (before.run?.runId ?? null) !== (now.run?.runId ?? null);
    if (before.run && runChanged) readers.delete(before.run.runId);
    // 지켜보던 실행이 바뀌었는데 앞 실행의 조회가 걸려 있으면 끊습니다 — 새 실행을 시간 초과(20초)까지 기다리지 않게
    const hungPoll = runChanged && polling ? pollController : null;
    if (now.run && runChanged) {
      // 새 실행이 붙었다는 것은 서버가 다시 받았다는 뜻이라 멈춤을 풉니다
      halted = null;
      watch(now.run.runId);
      kick();
    }
    hungPoll?.abort();
    // 확인 중에 다른 대화를 고르면 앞 대화의 실패 대기를 기다리지 않고 바로 조회합니다
    const otherConversation = now.conversationId !== before.conversationId;
    if (now.syncing && now.conversationId && (!before.syncing || otherConversation)) {
      if (otherConversation) lookupFailures = 0;
      const hung = lookingUp ? lookupController : null;
      lookup().catch(() => undefined);
      // 앞 대화의 조회가 걸려 있으면 끊습니다 — 끝나기를 기다리지 않고 곧바로 새 대화를 조회합니다
      hung?.abort();
    }
  });

  const offOnline = (options.onOnline ?? defaultOnOnline)(() => {
    // 꺼짐 · 로그인 만료로 멈췄으면 연결이 돌아와도 다시 두드리지 않습니다 — `resume()` · `checkNow()` 가 엽니다
    if (halted) return;
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
      halted = null;
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
        const view = readRunView(await timed((signal) => api.cancel(run.runId, signal)));
        // 닫혔어도 잠금을 푸는 저장소 정리는 합니다 — 새 통신 계층은 이 취소를 모르므로, 그대로 두면
        // "취소 중" 이 실행이 끝날 때까지 남아 다시 취소할 수 없습니다. 신호 · 다시 조회만 건너뜁니다
        if (disposed) {
          if (view && view.run_id === run.runId && isTerminalRun(view.status))
            store().endRun(run.runId, view.retry_after_s);
          else store().abortCancel(run.runId);
          return true;
        }
        // 끝남은 그 실행을 아직 지켜볼 때만 알립니다 — 조회가 먼저 끝냈으면 취소 중 표시만 지웁니다
        if (view && view.run_id === run.runId && isTerminalRun(view.status))
          finishRun(run.runId, run.conversationId, view);
        else {
          // 서버가 취소하지 않고 그 상태를 그대로 돌려줌(예: 다른 탭이 먼저 승인해 RUNNING) —
          // "취소 중" 을 풀어 보내기 · 다시 취소를 막지 않고, 상태부터 다시 확인합니다
          store().abortCancel(run.runId);
          if (store().run?.runId === run.runId) {
            emit({ type: 'cancelFailed', runId: run.runId });
            kick();
          }
        }
      } catch (error) {
        if (disposed) {
          store().abortCancel(run.runId);
          return true;
        }
        const mine = store().run?.runId === run.runId;
        // 꺼짐 — 취소되지 않았습니다. 잠금을 풀고 버튼을 숨기게 알립니다
        if (isDisabled(error)) {
          store().abortCancel(run.runId);
          emit({ type: 'disabled' });
          return true;
        }
        // 서버가 거절했으면(4xx) 취소되지 않은 것이 확실합니다 — 풀고 다시 취소할 수 있게 둡니다
        if (error instanceof ApiFailure && error.status < 500) {
          store().abortCancel(run.runId);
          if (mine) {
            emit({ type: 'cancelFailed', runId: run.runId });
            kick();
          }
          return true;
        }
        // 응답을 잃음(끊김 · 시간 초과 · 5xx) — 서버가 취소했을 수 있습니다. "취소 중" 은 그대로 두고
        // 같은 실행의 상태부터 확인해 확정합니다(L3-34 "응답 유실 때 같은 run_id 상태부터 확인")
        if (!mine) {
          store().abortCancel(run.runId);
          return true;
        }
        cancelUnsure = run.runId;
        emit({ type: 'cancelChecking', runId: run.runId });
        kick();
      }
      return true;
    },

    decide: async (actionId, decision, argsHash) => {
      if (disposed) return 'stale';
      const run = store().run;
      if (!run || run.phase !== 'WAITING_APPROVAL' || deciding.has(actionId)) return 'locked';
      deciding.add(actionId);
      const ticket = chatTicket();
      const request = { decision, args_hash: argsHash };
      const once = () => timed((signal) => api.decide(run.runId, actionId, request, signal));
      try {
        // 응답을 잃으면 같은 요청으로 한 번 다시 보냅니다 — BE 는 같은 결정에 처음 결과(그때의 상태)를 줍니다(L3-39)
        const raw = await once().catch((error: unknown) => {
          if (!isUnknownResult(error) || disposed || !isCurrent(ticket)) throw error;
          return once();
        });
        const view = readRunView(raw);
        if (!view || view.run_id !== run.runId) throw new FormatError();
        if (disposed || !isCurrent(ticket)) return 'stale';
        // 거절이면 DONE 으로 끝납니다. 승인이면 RUNNING — 이어지는 문장은 자기 커서로 조회합니다
        if (isTerminalRun(view.status)) finishRun(run.runId, run.conversationId, view);
        else {
          if (KNOWN.includes(view.status)) store().setRunPhase(run.runId, asPhase(view.status));
          kick();
        }
        return 'decided';
      } catch (error) {
        if (disposed || !isCurrent(ticket)) return 'stale';
        if (isDisabled(error)) {
          emit({ type: 'disabled' });
          return 'failed';
        }
        emit({
          type: 'decisionFailed',
          runId: run.runId,
          actionId,
          code: decisionCode(error),
        });
        // 카드가 바뀌었거나 끝났을 수 있습니다 — 지금 상태로 카드를 다시 읽습니다
        kick();
        return 'failed';
      } finally {
        deciding.delete(actionId);
      }
    },

    changeScope: async (scope) => {
      if (disposed) return false;
      if (store().conversationId === null) return store().chooseScope(scope);
      const ticket = store().beginScopeChange(scope);
      if (!ticket) return false;
      try {
        const changed = await timed((signal) =>
          api.changeScope(ticket.conversationId!, scope.pitchId, signal),
        );
        store().finishScopeChange(ticket, {
          pitchId: changed.scope_pitch_id,
          title: changed.scope_pitch_title,
        });
        return true;
      } catch (error) {
        // 결과를 모름(끊김 · 시간 초과 · 5xx) — 서버가 바꿨을 수 있어 대화 목록에서 서버의 범위를 읽어 맞춥니다
        if (isUnknownResult(error) && !disposed && isCurrent(ticket)) {
          const server = await serverScope(ticket.conversationId!);
          if (server && isCurrent(ticket)) {
            store().finishScopeChange(ticket, server);
            if (server.pitchId === scope.pitchId) return true;
            emit({ type: 'scopeFailed', code: 'REQUEST_FAILED' });
            return false;
          }
        }
        const current = isCurrent(ticket);
        store().finishScopeChange(ticket, null);
        if (!current || disposed) return false;
        if (isDisabled(error)) {
          emit({ type: 'disabled' });
          return false;
        }
        emit({
          type: 'scopeFailed',
          code: error instanceof ApiFailure ? error.code : 'REQUEST_FAILED',
        });
        return false;
      }
    },

    checkNow: () => {
      if (disposed) return;
      halted = null;
      const run = store().run;
      if (run) {
        // 직접 확인하면 늦어짐을 처음부터 다시 셉니다 — 받은 문장 · 커서는 그대로 둬 같은 문장을 다시 받지 않습니다
        runClocks.delete(run.runId);
        slowNotified.delete(run.runId);
        ensureClock(run.runId);
        idleDelay = 0;
        rereads = 0;
        kick();
      } else if (store().syncing) {
        lookupFailures = 0;
        ensureLookup();
      }
    },

    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    dispose: () => {
      disposed = true;
      pollController?.abort();
      lookupController?.abort();
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
