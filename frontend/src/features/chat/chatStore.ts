import { create } from 'zustand';
import { onSignedOut } from '@/shared/lib/sessionSignal';
import type { SendMessageRequest } from '@/types/chat';

/**
 * 대화형 코치 에이전트의 화면 상태 (`docs/ai/features/chat-agent.md` 12-4 F1 · F2 · F3).
 *
 * 왜 Zustand인가 — 대화창은 화면을 옮겨도 이어져야 하고(라우팅으로 컴포넌트가 죽습니다), 서버에서 온 값이
 * 아니며(메시지 · 실행 상태는 TanStack Query), 프레임 단위도 아닙니다. frontend/AGENTS.md 상태 배치표의 가운데 칸입니다.
 *
 * 서버를 부르는 일은 통신 계층이 하고, 이 저장소는 값과 그 값을 바꿔도 되는지만 정합니다.
 * 통신 계층이 할 순서 — 실제 코드는 `chatTransport.ts` 입니다:
 *   새 대화  '기억 없이' 면 보내기 전에 대화부터 만듭니다(12-3 "'기억 없이' 선택(대화를 만들 때만)") —
 *            needsCreate 면 beginCreate → POST /conversations(memory_off) → finishCreate(표, 대화 ID) → 보내기
 *   보내기   beginSend(초안) → `pending.request` 를 그대로 POST → 202 면 beginRun(표)
 *            · 15초 넘게 모르면 markUnconfirmed — **자동으로 다시 보내지 않고**(L3-18 "사용자 재시도만"), 사용자가
 *              [다시 확인]을 누를 때 같은 `pending.request` 로 다시 보냅니다
 *            · 접수 안 됨이 확정되면 failSend(표, 기다릴 초) — 429 는 Retry-After, 없거나 잘못되면 5초
 *            · 409 AGENT_RUN_IN_PROGRESS(다른 탭이 실행 중)면 conflictSend(표) → 대화의 실행 조회 →
 *              resolveConversation — 조회가 끝날 때까지 잠근 채로 둡니다(L3-17 "409 실행 중은 상태 조회")
 *   조회     chatTicket → GET → isCurrent 면 setRunPhase · advanceCursor → 종결이면 endRun(실행, retry_after_s)
 *            — 범위가 바뀐 종결 응답은 endRun **전에** applyServerScope(그 조회의 표, 범위) 합니다
 *   취소     beginCancel → POST → 종결을 보면 endRun · 취소 요청이 실패하면 abortCancel
 *   대화 열기 selectConversation · 새로고침 복원 → 대화의 실행 조회 → resolveConversation(표, 찾은 실행)
 *            — 되살린 보내기를 다시 보내는 것과 동시에 해도 됩니다. 202 로 실행이 먼저 붙으면 늦게 온 조회는 버려집니다
 *   되살린 보내기 새로고침 전에 보내던 요청이 있으면(`pending`) 미확정으로 알리고, 사용자가 [다시 확인]을 누르면
 *            대화가 있든 없든 `pendingSendTicket()` 과 `pending.request` 로 같은 키를 다시 보냅니다(L3-15 "같은 키 ·
 *            본문으로 재시도") — 접수됐던 요청이면 BE 가 같은 202 를, 못 받았던 요청이면 새로 접수합니다.
 *            접수되지 않음이 확정될 때만 failSend 합니다
 *   범위     새 대화는 chooseScope, 있는 대화는 beginScopeChange → PUT → finishScopeChange(표, 결과)
 *
 * **잠그는 것** (12-4 F2 · F3) — 화면은 `canSend` · `canChangeScope` 로 읽습니다
 * - 보내기: 로그인 전 · 범위 확인 중 · 대화 만드는 중 · 보내는 중(미확정 포함) · 취소 확정 전 · 범위 변경 중 ·
 *   대화 상태를 아직 모를 때 ·
 *   대기 · 실행 중 · 기다리라는 시각 전(429 Retry-After · `retry_after_s`). 승인 대기에서는 허용합니다 — BE 가 앞 확인을
 *   취소하고 받습니다.
 * - 범위 칩: 위와 같고, 승인 대기까지 실행이 끝나기 전에는 막습니다("실행 중에는 칩을 막는다").
 * - 새 대화 · 대화 고르기: 막지 않습니다. 앞 대화의 늦은 응답은 표(`isCurrent`)로 버립니다
 *   ("다른 대화로 옮긴 뒤 도착한 이전 대화의 늦은 종결 응답은 무시").
 *
 * 새로고침해도 이어 가도록(12-4 "복원") 대화 ID · 범위 · 실행 ID · 보내는 중인 요청을 sessionStorage 에 사용자별로 둡니다.
 * 커서는 남기지 않습니다 — 받은 문장은 메모리에만 있어 새로고침하면 사라지므로, 되살린 실행은 처음부터 다시 받아
 * 효과 없이 그립니다("받은 문장은 효과 없이 그린다"). 다른 사용자의 사본은 그 사용자가 아니게 되는 순간 지웁니다
 * ("로컬 임시 ID 는 사용자별로 나누고 만료 때 버린다").
 */

/** 대화 범위 — 전체(`pitchId: null`) 또는 피치 하나. 칩에 보일 이름을 함께 둡니다 */
export interface ChatScope {
  pitchId: string | null;
  title: string | null;
}

export const WHOLE_SCOPE: ChatScope = { pitchId: null, title: null };

/**
 * 화면이 아는 범위. `null` 은 "범위 확인 중" — Take 화면이 그 Take 를 조회하기 전처럼 피치를 아직 모를 때입니다.
 * 전체로 단정하지 않고, 확인 중에는 보내지 않습니다 (12-4 F2 "범위를 모를 때")
 */
export type ScreenScope = ChatScope | null;

/** 지켜보는 실행의 단계. 종결되면 실행을 지웁니다 */
export type RunPhase = 'QUEUED' | 'RUNNING' | 'WAITING_APPROVAL';

/** 지켜보는 실행. 통신 계층이 이것으로 조회하고, 커서는 앞으로만 갑니다 */
export interface ActiveRun {
  runId: string;
  conversationId: string;
  phase: RunPhase;
  /** 마지막으로 처리한 이벤트 순번(`after_seq`). 같은 순번은 한 번만 처리합니다 */
  afterSeq: number;
}

/**
 * 보내는 중인 요청 — 접수(202)가 오기 전. `sendId` 는 그 보내기 하나의 표시이고 `client_request_id` 로 그대로 씁니다.
 * 확정되지 않으면(미확정) 잠금을 풀지 않고 같은 sendId 로 다시 보냅니다("새 실행을 임의로 만들지 않는다")
 */
export interface PendingSend {
  sendId: string;
  /** 보낸 대화. 새 대화면 null — 대화 ID 는 202 로 받습니다 */
  conversationId: string | null;
  /** 보낸 범위 — 보내는 순간 고정됩니다 */
  scope: ChatScope;
  /** 보내기 전에 범위가 정해져 있었는지 — 새 대화의 첫 보내기가 실패하면 이것으로 돌립니다 */
  scopeFixedBefore: boolean;
  /**
   * 보낼 본문 그대로. 다시 보낼 때도 이것을 보냅니다 — 같은 키에 다른 본문은 409 `AGENT_REQUEST_CONFLICT` 입니다.
   * 새로고침 뒤에도 다시 보낼 수 있게 함께 남깁니다
   */
  request: SendMessageRequest;
  unconfirmed: boolean;
}

/** 보내기 초안 — 대화 · 범위 · `client_request_id` 는 저장소가 채웁니다 */
export type SendDraft = Pick<
  SendMessageRequest,
  'text' | 'page' | 'again_of_message_id' | 'choice'
>;

/**
 * 응답이 지금 화면의 것인지 가릴 표 — 요청을 보낼 때 받아 두고, 응답이 오면 `isCurrent` 로 견줍니다.
 * - `attempt`: 새 대화 · 대화 고르기 · 사용자 바뀜마다 올라 앞 화면의 응답을 가립니다
 * - `sendId`: 보내기 표에만 있고, 그 보내기 하나를 가립니다
 * - `scopeChangeId`: 범위 변경 표에만 있습니다
 */
export interface ChatTicket {
  generation: number;
  attempt: number;
  conversationId: string | null;
  runId: string | null;
  sendId: string | null;
  scopeChangeId: number | null;
  /** 대화 만들기 표에만 있습니다 */
  createId: number | null;
}

/** 찾은 실행 — 대화를 열거나 되살린 뒤 실행 상태 조회로 압니다 */
export interface FoundRun {
  runId: string;
  phase: RunPhase;
}

interface ChatState {
  /** 지금 로그인한 사용자. 로그인 전이거나 모르면 null */
  userId: string | null;
  /** 세션 세대 — 사용자가 바뀌거나 로그아웃하면 올라가고, 앞 세대의 응답은 버립니다 */
  generation: number;
  /** 화면 시도 번호 — 새 대화 · 대화 고르기 · 사용자 바뀜마다 올라갑니다 */
  attempt: number;
  open: boolean;
  /** 보고 있는 대화. 새 대화 입력 상태면 null */
  conversationId: string | null;
  /** 지금 범위. null 이면 범위 확인 중 */
  scope: ScreenScope;
  /** 범위를 정했는지 — 칩으로 고르거나 보내기를 시작하면, 화면을 옮겨도 덮어쓰지 않습니다 */
  scopeFixed: boolean;
  /** '기억 없이' 대화인지 — 새 대화에서는 고른 값, 고른 대화에서는 그 대화의 값. 대화를 만든 뒤에는 바꾸지 않습니다 */
  memoryOff: boolean;
  pending: PendingSend | null;
  run: ActiveRun | null;
  /** 취소를 눌렀고 아직 확정되지 않은 실행 */
  cancelling: string | null;
  /** 고르거나 되살린 대화의 실행 상태를 아직 모름 — 첫 조회까지 잠급니다 */
  syncing: boolean;
  /** 이 시각(ms) 전에는 보내지 않습니다 — 429 `Retry-After` · 실행 오류의 `retry_after_s` (12-4 F3 · F8) */
  blockedUntil: number | null;
  /** '기억 없이' 새 대화를 만드는 중 — 응답까지 보내기 · 칩을 잠급니다 */
  creating: { id: number; scopeFixedBefore: boolean } | null;
  /** 진행 중인 범위 변경 — 응답이 오기 전까지 칩과 보내기를 잠급니다 */
  scopeChange: { id: number; previous: ScreenScope } | null;
  /** 대화창을 가린 모달 수와, 가려진 동안 정해진 열림 상태 — 모달이 모두 닫히면 이것으로 돌립니다 */
  coveredBy: number;
  openAfterCover: boolean;

  /**
   * 로그인 사용자가 **확인됐을 때만** 부릅니다. 다른 사용자면 비우고 세대를 올린 뒤, 그 사용자의 사본을 되살립니다.
   * `null` 은 로그인하지 않았음이 확인된 때입니다 — 세션을 확인하는 중이거나 오류일 때 부르면 복원용 사본까지 지웁니다
   */
  setUser: (userId: string | null) => void;
  /** 로그아웃(이 탭 · 다른 탭) — 비우고 세대를 올립니다 */
  signOut: () => void;
  /**
   * 대화창을 엽니다. 범위를 아직 정하지 않았으면 연 화면의 범위로 맞춥니다
   * (12-4 "처음 범위는 대화창을 연 화면으로 정한다" · "화면을 옮겨도 열린 대화의 범위는 바뀌지 않는다")
   */
  openChat: (screenScope: ScreenScope) => void;
  closeChat: () => void;
  /** 화면이 범위를 새로 알았을 때(예: Take 조회가 끝나 피치를 앎). 범위를 아직 정하지 않았을 때만 맞춥니다 */
  syncScreenScope: (screenScope: ScreenScope) => void;
  /** 새 대화 입력 상태로. 보내던 요청 · 지켜보던 실행은 놓습니다 — 늦은 응답은 표로 버립니다 */
  startNewConversation: (screenScope: ScreenScope) => void;
  /**
   * 대화 목록에서 지난 대화를 고릅니다. 그 대화의 실행 상태를 조회할 때까지 잠급니다(`resolveConversation`).
   * `memoryOff` 는 목록의 그 대화 값입니다 — '기억 없이' 표시를 그 대화대로 붙입니다
   */
  selectConversation: (conversationId: string, scope: ChatScope, memoryOff?: boolean) => void;
  /**
   * 대화를 열거나 되살린 뒤 · 409 뒤 실행 상태를 조회한 결과. 표가 지금 것일 때만 받고, 같은 실행이면 커서를 지킵니다.
   * 보내는 중(`pending`)은 건드리지 않습니다 — 실행이 없다고 그 말을 서버가 못 받았다고 볼 수 없고, 다른 탭의 실행을
   * 그 말의 답으로 볼 수도 없습니다. 보내는 중은 같은 키로 다시 보내 정리합니다
   */
  resolveConversation: (ticket: ChatTicket, found: FoundRun | null) => boolean;
  /** 새 대화의 범위 칩 — 서버를 부르지 않습니다. 대화가 있거나 잠겨 있으면 false */
  chooseScope: (scope: ChatScope) => boolean;
  /** 있는 대화의 범위 바꾸기를 시작합니다(BE '범위 바꾸기'). 잠겨 있으면 null — 응답까지 칩과 보내기를 잠급니다 */
  beginScopeChange: (scope: ChatScope) => ChatTicket | null;
  /** 범위 바꾸기 응답. 실패(null)면 앞 범위로 돌립니다. 표가 지금 것일 때만 받습니다 */
  finishScopeChange: (ticket: ChatTicket, result: ChatScope | null) => void;
  /**
   * 서버가 확정한 범위로 칩을 바꿉니다 — 에이전트가 다른 피치를 연 뒤 BE 가 검증된 `final` 로 확정한 때(12-4 F2).
   * 그 응답의 표가 지금 것이고 사용자가 바꾸는 중이 아닐 때만 받습니다
   */
  applyServerScope: (ticket: ChatTicket, scope: ChatScope) => void;
  /** 새 대화의 '기억 없이'. 대화가 있거나 · 만드는 중이거나 · 보내기를 시작했으면 바꾸지 않고 false */
  setMemoryOff: (memoryOff: boolean) => boolean;
  /**
   * 보내기를 시작합니다 — 누르는 즉시 잠급니다("전송을 시작하면 바로 잠그고"). 보낼 수 없으면 null.
   * 보낼 본문은 `pending.request` 입니다
   */
  beginSend: (draft: SendDraft) => ChatTicket | null;
  /** '기억 없이' 새 대화를 만들기 시작합니다. 만들 필요가 없거나 잠겨 있으면 null */
  beginCreate: () => ChatTicket | null;
  /** 대화 만들기 응답 — 만든 대화 ID, 실패면 null. 그 만들기의 표일 때만 받습니다 */
  finishCreate: (ticket: ChatTicket, conversationId: string | null) => void;
  /**
   * 보내기가 409 `AGENT_RUN_IN_PROGRESS` 였을 때 — 이 대화에 다른 실행(다른 탭)이 있습니다. 보내던 요청은 접수되지
   * 않았으니 놓고, 대화의 실행을 조회할 때까지 잠급니다(L3-17)
   */
  conflictSend: (ticket: ChatTicket) => void;
  /** 접수가 확인되지 않은 채 시간이 지남 — 잠금은 그대로 두고 같은 sendId 로 다시 보냅니다 */
  markUnconfirmed: (ticket: ChatTicket) => void;
  /**
   * 접수되지 않았음이 확정됐을 때(4xx 등). 그 보내기일 때만 잠금을 풉니다.
   * `retryAfterSec` 가 있으면 그때까지 보내기를 막습니다(429 — 헤더가 없거나 잘못됐으면 통신 계층이 5초로)
   */
  failSend: (ticket: ChatTicket, retryAfterSec?: number | null) => void;
  /**
   * 접수(202)를 받았을 때. 그 보내기의 표일 때만 붙이고 true — 새 대화였으면 서버가 준 대화 ID 를 이때 받습니다.
   * 같은 실행이 다시 오면(같은 키로 다시 보냄) 실행과 커서를 그대로 둡니다
   */
  beginRun: (ticket: ChatTicket, runId: string, conversationId: string) => boolean;
  /** 실행 조회로 단계가 바뀌었을 때. 다른 실행이거나 대기로 되돌아가는 값이면 무시합니다 */
  setRunPhase: (runId: string, phase: RunPhase) => void;
  /** 처리한 이벤트 순번까지 커서를 옮깁니다. 다른 실행이거나 뒤로 가는 값이면 무시하고 false */
  advanceCursor: (runId: string, seq: number) => boolean;
  /**
   * 커서를 처음(0)으로 — 문장을 읽는 쪽(통신 계층)이 새로 생겨 앞 문장을 들고 있지 않을 때,
   * 처음부터 다시 받아 순번이 0부터 이어지게 합니다. 다른 실행이면 무시하고 false
   */
  rewindCursor: (runId: string) => boolean;
  /**
   * 실행이 종결됐을 때. 그 실행의 취소 중 표시도 지웁니다(지켜보던 실행이 바뀐 뒤여도).
   * `retryAfterSec`(실행 오류의 `retry_after_s`)가 있으면 그때까지 보내기를 막습니다
   */
  endRun: (runId: string, retryAfterSec?: number | null) => void;
  /** [취소]를 눌렀을 때. 확정될 때까지 새 보내기를 막습니다("취소 확정 전에는 … 새 실행을 허용하지 않는다") */
  beginCancel: (runId: string) => boolean;
  /** 취소 요청 자체가 실패했을 때 — 실행은 그대로이고 다시 취소할 수 있습니다 */
  abortCancel: (runId: string) => void;
  /** 다른 모달이 열리고 닫힐 때 — 모두 닫히면 가려진 동안 정해진 열림 상태로 돌립니다 */
  coverByModal: () => void;
  uncoverByModal: () => void;
}

/** 보낼 수 있는지 — 버튼 · 입력의 잠금. 기다리라는 시각이 지났는지는 `now` 로 봅니다 */
export function canSend(s: ChatState, now = Date.now()): boolean {
  if (s.userId === null || s.scope === null) return false;
  if (s.blockedUntil !== null && now < s.blockedUntil) return false;
  if (s.pending || s.creating || s.cancelling || s.scopeChange || s.syncing) return false;
  return s.run === null || s.run.phase === 'WAITING_APPROVAL';
}

/** '기억 없이' 새 대화라 보내기 전에 대화를 먼저 만들어야 하는지 */
export function needsCreate(s: ChatState): boolean {
  return s.conversationId === null && s.memoryOff;
}

/** 범위 칩을 바꿀 수 있는지 */
export function canChangeScope(s: ChatState): boolean {
  if (s.userId === null) return false;
  return !s.pending && !s.creating && !s.run && !s.cancelling && !s.scopeChange && !s.syncing;
}

// ── 새로고침 뒤 복원용 사본 ───────────────────────────────────

const STORAGE_PREFIX = 'pitchcoach.chat.';
const storageKey = (userId: string) => `${STORAGE_PREFIX}${userId}`;

/** 남기는 것 — 커서 · 단계는 남기지 않습니다(되살리면 처음부터 다시 받고, 단계는 조회로 압니다) */
interface Saved {
  conversationId: string | null;
  scope: ScreenScope;
  scopeFixed: boolean;
  memoryOff: boolean;
  run: { runId: string; conversationId: string } | null;
  pending: Omit<PendingSend, 'unconfirmed'> | null;
  blockedUntil: number | null;
}

function readSaved(userId: string): Saved | null {
  try {
    const raw = sessionStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<Saved> | null;
    if (typeof saved !== 'object' || saved === null) return null;
    return {
      conversationId: isId(saved.conversationId) ? saved.conversationId : null,
      scope: isScope(saved.scope) ? saved.scope : null,
      scopeFixed: saved.scopeFixed === true,
      memoryOff: saved.memoryOff === true,
      run: isSavedRun(saved.run) ? saved.run : null,
      pending: isSavedPending(saved.pending) ? saved.pending : null,
      blockedUntil:
        typeof saved.blockedUntil === 'number' && Number.isFinite(saved.blockedUntil)
          ? saved.blockedUntil
          : null,
    };
  } catch {
    return null;
  }
}

function writeSaved(userId: string, saved: Saved) {
  try {
    sessionStorage.setItem(storageKey(userId), JSON.stringify(saved));
  } catch {
    // 저장소를 못 쓰면(사생활 보호 창 등) 새로고침 뒤 복원만 빠집니다
  }
}

/** 이 사용자 말고 다른 사용자의 사본을 지웁니다. null 이면 모두 지웁니다 */
function dropOthers(userId: string | null) {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(STORAGE_PREFIX) && (userId === null || key !== storageKey(userId)))
        sessionStorage.removeItem(key);
    }
  } catch {
    // 저장소를 못 쓰면 지울 것도 없습니다
  }
}

const isId = (v: unknown): v is string => typeof v === 'string' && v !== '';

const isScope = (v: unknown): v is ChatScope =>
  typeof v === 'object' &&
  v !== null &&
  ((v as ChatScope).pitchId === null || isId((v as ChatScope).pitchId)) &&
  ((v as ChatScope).title === null || typeof (v as ChatScope).title === 'string');

const isSavedRun = (v: unknown): v is NonNullable<Saved['run']> =>
  typeof v === 'object' &&
  v !== null &&
  isId((v as ActiveRun).runId) &&
  isId((v as ActiveRun).conversationId);

const isSavedPending = (v: unknown): v is NonNullable<Saved['pending']> =>
  typeof v === 'object' &&
  v !== null &&
  isId((v as PendingSend).sendId) &&
  ((v as PendingSend).conversationId === null || isId((v as PendingSend).conversationId)) &&
  isScope((v as PendingSend).scope) &&
  typeof (v as PendingSend).scopeFixedBefore === 'boolean' &&
  isSavedRequest((v as PendingSend).request, (v as PendingSend).sendId);

/** 다시 보낼 본문 — 그 보내기의 키를 달고, 글이 있어야 합니다 */
const isSavedRequest = (v: unknown, sendId: string): v is SendMessageRequest =>
  typeof v === 'object' &&
  v !== null &&
  (v as SendMessageRequest).client_request_id === sendId &&
  typeof (v as SendMessageRequest).text === 'string' &&
  typeof (v as SendMessageRequest).page === 'object' &&
  (v as SendMessageRequest).page !== null;

/** 기다릴 초 → 그 시각. 쓸 수 없는 값이면 null */
const blockFor = (retryAfterSec: number | null | undefined, now = Date.now()) =>
  typeof retryAfterSec === 'number' && Number.isFinite(retryAfterSec) && retryAfterSec > 0
    ? now + retryAfterSec * 1000
    : null;

/** 보내기 하나의 표시 — `client_request_id` 로 씁니다 */
/**
 * 보내기 키 — BE 는 UUID 만 받습니다(`client_request_id: uuid.UUID`, 아니면 422).
 * `randomUUID` 는 보안 연결(https · localhost)에서만 있어, 없으면 같은 모양(v4)을 직접 만듭니다
 */
function newSendId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function')
    crypto.getRandomValues(bytes);
  else for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// ── 저장소 ───────────────────────────────────────────────────

/** 사용자마다 비우는 것 — 모달 가림은 사용자와 상관없는 화면 상태라 두지 않습니다 */
const emptyForUser = {
  open: false,
  conversationId: null,
  scope: null,
  scopeFixed: false,
  memoryOff: false,
  pending: null,
  run: null,
  cancelling: null,
  syncing: false,
  scopeChange: null,
  creating: null,
  blockedUntil: null,
  openAfterCover: false,
} satisfies Partial<ChatState>;

/** 대화를 옮길 때 놓는 것 */
const leaveConversation = {
  pending: null,
  run: null,
  cancelling: null,
  scopeChange: null,
  creating: null,
  memoryOff: false,
} satisfies Partial<ChatState>;

let scopeChangeSeq = 0;
let createSeq = 0;

export const useChatStore = create<ChatState>()((set, get) => {
  /** 바뀐 뒤 복원용 사본을 남깁니다 */
  const save = () => {
    const {
      userId,
      conversationId,
      scope,
      scopeChange,
      scopeFixed,
      memoryOff,
      run,
      pending,
      blockedUntil,
    } = get();
    if (!userId) return;
    writeSaved(userId, {
      conversationId,
      // 범위 변경 중이면 서버가 아직 받지 않은 범위라 앞 범위를 남깁니다
      scope: scopeChange ? scopeChange.previous : scope,
      scopeFixed,
      memoryOff,
      run: run ? { runId: run.runId, conversationId: run.conversationId } : null,
      pending: pending
        ? {
            sendId: pending.sendId,
            conversationId: pending.conversationId,
            scope: pending.scope,
            scopeFixedBefore: pending.scopeFixedBefore,
            request: pending.request,
          }
        : null,
      blockedUntil,
    });
  };
  /** 열림을 바꿉니다 — 모달에 가려진 동안에는 모달이 닫힌 뒤의 상태만 바꿉니다 */
  const setOpen = (open: boolean) => set(get().coveredBy > 0 ? { openAfterCover: open } : { open });
  /** 이 보내기 표가 지금 보내는 중인 그 보내기인지 */
  const isPendingSend = (ticket: ChatTicket) => {
    const s = get();
    return (
      ticket.sendId !== null &&
      ticket.generation === s.generation &&
      ticket.attempt === s.attempt &&
      s.pending?.sendId === ticket.sendId
    );
  };

  return {
    userId: null,
    generation: 0,
    attempt: 0,
    coveredBy: 0,
    ...emptyForUser,

    setUser: (userId) => {
      if (userId === get().userId) {
        // 새로 연 페이지는 처음부터 사용자가 없음이라, 로그아웃이 확인돼도 여기서 끝납니다 — 그래도 사본은 지웁니다
        if (userId === null) dropOthers(null);
        return;
      }
      dropOthers(userId);
      const saved = userId ? readSaved(userId) : null;
      set((s) => ({
        ...emptyForUser,
        // 가려진 동안 사용자가 바뀌면, 모달이 닫힌 뒤에도 닫힌 채로 둡니다
        open: false,
        userId,
        generation: s.generation + 1,
        attempt: s.attempt + 1,
        ...(saved
          ? {
              conversationId: saved.conversationId,
              scope: saved.scope,
              scopeFixed: saved.scopeFixed,
              memoryOff: saved.memoryOff,
              // 단계는 모르니 끝나기 전으로 둡니다 — 첫 조회가 맞춥니다
              run: saved.run && { ...saved.run, phase: 'RUNNING' as const, afterSeq: 0 },
              // 보내던 요청은 결과를 모르니 미확정으로 — 같은 sendId 로 다시 보내 확인합니다
              pending: saved.pending && { ...saved.pending, unconfirmed: true },
              // 있는 대화는 실행 상태를 다시 읽을 때까지 잠급니다
              syncing: saved.conversationId !== null,
              blockedUntil: saved.blockedUntil,
            }
          : {}),
      }));
    },

    signOut: () => {
      dropOthers(null);
      set((s) => ({
        ...emptyForUser,
        userId: null,
        generation: s.generation + 1,
        attempt: s.attempt + 1,
      }));
    },

    openChat: (screenScope) => {
      if (!get().scopeFixed) set({ scope: screenScope });
      setOpen(true);
      save();
    },

    closeChat: () => setOpen(false),

    syncScreenScope: (screenScope) => {
      if (get().scopeFixed) return;
      set({ scope: screenScope });
      save();
    },

    startNewConversation: (screenScope) => {
      set((s) => ({
        ...leaveConversation,
        conversationId: null,
        scope: screenScope,
        scopeFixed: false,
        syncing: false,
        attempt: s.attempt + 1,
      }));
      save();
    },

    selectConversation: (conversationId, scope, memoryOff = false) => {
      set((s) => ({
        ...leaveConversation,
        conversationId,
        memoryOff,
        scope,
        scopeFixed: true,
        syncing: true,
        attempt: s.attempt + 1,
      }));
      save();
    },

    resolveConversation: (ticket, found) => {
      const s = get();
      if (
        // 상태를 모를 때만 받습니다 — 그사이 202 로 실행이 붙었거나 새 대화로 옮겼으면 이 조회는 늦은 응답입니다
        // (L3-20 "느린 응답 순서 역전 · 이전 대화 응답이 현재 진행 상태를 덮음")
        !s.syncing ||
        ticket.generation !== s.generation ||
        ticket.attempt !== s.attempt ||
        ticket.conversationId === null ||
        ticket.conversationId !== s.conversationId
      )
        return false;
      const conversationId = ticket.conversationId;
      const same = found !== null && s.run?.runId === found.runId;
      set({
        syncing: false,
        // 다른 실행이 붙으면 앞 실행의 취소 중 표시는 의미가 없습니다
        cancelling: found && s.cancelling === found.runId ? s.cancelling : null,
        run: found && {
          runId: found.runId,
          conversationId,
          phase: found.phase,
          afterSeq: same ? s.run!.afterSeq : 0,
        },
      });
      save();
      return true;
    },

    chooseScope: (scope) => {
      const s = get();
      if (s.conversationId !== null || !canChangeScope(s)) return false;
      set({ scope, scopeFixed: true });
      save();
      return true;
    },

    beginScopeChange: (scope) => {
      const s = get();
      if (s.conversationId === null || !canChangeScope(s)) return null;
      const id = ++scopeChangeSeq;
      set({ scopeChange: { id, previous: s.scope }, scope });
      return { ...chatTicket(), scopeChangeId: id };
    },

    finishScopeChange: (ticket, result) => {
      const s = get();
      if (
        ticket.scopeChangeId === null ||
        s.scopeChange?.id !== ticket.scopeChangeId ||
        ticket.generation !== s.generation ||
        ticket.attempt !== s.attempt
      )
        return;
      set({ scopeChange: null, scope: result ?? s.scopeChange.previous, scopeFixed: true });
      save();
    },

    applyServerScope: (ticket, scope) => {
      if (get().scopeChange || !isCurrent(ticket)) return;
      set({ scope, scopeFixed: true });
      save();
    },

    setMemoryOff: (memoryOff) => {
      const s = get();
      // 만드는 요청이 나간 순간 그 대화의 값이 정해집니다 — 응답 전에도 바꾸지 않습니다 (F15 "만든 뒤에는 바꾸지 않는다")
      if (s.conversationId !== null || s.pending || s.creating) return false;
      set({ memoryOff });
      save();
      return true;
    },

    beginSend: (draft) => {
      const s = get();
      if (!canSend(s) || needsCreate(s)) return null;
      const sendId = newSendId();
      const pending: PendingSend = {
        sendId,
        conversationId: s.conversationId,
        scope: s.scope!,
        scopeFixedBefore: s.scopeFixed,
        request: {
          ...draft,
          conversation_id: s.conversationId,
          scope_pitch_id: s.scope!.pitchId,
          client_request_id: sendId,
        },
        unconfirmed: false,
      };
      // 보내는 순간 범위가 정해집니다 — 접수 전에 화면을 옮겨도 바뀌지 않습니다
      set({ pending, scopeFixed: true });
      save();
      return { ...chatTicket(), sendId: pending.sendId };
    },

    beginCreate: () => {
      const s = get();
      if (!needsCreate(s) || !canSend(s)) return null;
      const id = ++createSeq;
      // 대화를 만드는 순간 범위가 정해집니다
      set({ creating: { id, scopeFixedBefore: s.scopeFixed }, scopeFixed: true });
      return { ...chatTicket(), createId: id };
    },

    finishCreate: (ticket, conversationId) => {
      const s = get();
      if (
        ticket.createId === null ||
        s.creating?.id !== ticket.createId ||
        ticket.generation !== s.generation ||
        ticket.attempt !== s.attempt
      )
        return;
      if (conversationId === null) {
        set({ creating: null, scopeFixed: s.creating.scopeFixedBefore });
        return;
      }
      // '기억 없이' 는 그대로 — 이 대화의 값입니다
      set({ creating: null, conversationId, scopeFixed: true });
      save();
    },

    conflictSend: (ticket) => {
      if (!isPendingSend(ticket)) return;
      set((s) => ({
        pending: null,
        scopeFixed: s.conversationId !== null || s.pending!.scopeFixedBefore,
        syncing: s.conversationId !== null,
      }));
      save();
    },

    markUnconfirmed: (ticket) => {
      if (!isPendingSend(ticket)) return;
      set({ pending: { ...get().pending!, unconfirmed: true } });
    },

    failSend: (ticket, retryAfterSec = null) => {
      if (!isPendingSend(ticket)) return;
      // 새 대화의 첫 보내기가 실패하면 대화가 없으니, 범위는 보내기 전처럼 다시 화면을 따라갈 수 있습니다
      set((s) => ({
        pending: null,
        scopeFixed: s.conversationId !== null || s.pending!.scopeFixedBefore,
        blockedUntil: blockFor(retryAfterSec) ?? s.blockedUntil,
      }));
      save();
    },

    beginRun: (ticket, runId, conversationId) => {
      const s = get();
      // 같은 실행이 다시 옴(같은 키로 다시 보냄) — 실행 · 커서는 그대로, 잠금만 풉니다
      if (s.run?.runId === runId) {
        if (isPendingSend(ticket)) set({ pending: null });
        save();
        return true;
      }
      if (!isPendingSend(ticket)) return false;
      set({
        pending: null,
        conversationId,
        // 보낸 범위로 정하는 것은 새 대화의 첫 보내기뿐 — 있는 대화는 그사이 서버가 확정한 범위를 지킵니다
        ...(s.pending!.conversationId === null ? { scope: s.pending!.scope } : {}),
        scopeFixed: true,
        syncing: false,
        // 앞 실행(승인 대기)은 BE 가 취소했습니다 — 그 실행의 취소 중 표시가 남아 잠기지 않게 지웁니다
        cancelling: null,
        run: { runId, conversationId, phase: 'QUEUED', afterSeq: 0 },
      });
      save();
      return true;
    },

    setRunPhase: (runId, phase) => {
      const { run } = get();
      if (run?.runId !== runId || run.phase === phase) return;
      // 접수 대기로는 되돌아가지 않습니다 — 순서가 뒤바뀐 조회 응답을 거릅니다
      if (phase === 'QUEUED') return;
      set({ run: { ...run, phase } });
    },

    advanceCursor: (runId, seq) => {
      const { run } = get();
      if (!run || run.runId !== runId || !Number.isInteger(seq) || seq <= run.afterSeq)
        return false;
      set({ run: { ...run, afterSeq: seq } });
      return true;
    },

    rewindCursor: (runId) => {
      const { run } = get();
      if (!run || run.runId !== runId) return false;
      if (run.afterSeq !== 0) set({ run: { ...run, afterSeq: 0 } });
      return true;
    },

    endRun: (runId, retryAfterSec = null) => {
      const s = get();
      // 그 실행의 취소 중 표시는 지켜보던 실행이 바뀐 뒤여도 지웁니다 — 남으면 보내기가 잠긴 채로 남습니다
      if (s.cancelling === runId) set({ cancelling: null });
      if (s.run?.runId !== runId) return;
      set({ run: null, blockedUntil: blockFor(retryAfterSec) ?? s.blockedUntil });
      save();
    },

    beginCancel: (runId) => {
      const s = get();
      if (s.run?.runId !== runId || s.cancelling === runId) return false;
      set({ cancelling: runId });
      return true;
    },

    abortCancel: (runId) => {
      if (get().cancelling === runId) set({ cancelling: null });
    },

    coverByModal: () => {
      set((s) =>
        s.coveredBy === 0
          ? { coveredBy: 1, openAfterCover: s.open, open: false }
          : { coveredBy: s.coveredBy + 1 },
      );
    },

    uncoverByModal: () => {
      set((s) => {
        if (s.coveredBy === 0) return {};
        if (s.coveredBy > 1) return { coveredBy: s.coveredBy - 1 };
        return { coveredBy: 0, open: s.openAfterCover, openAfterCover: false };
      });
    },
  };
});

/**
 * 로그아웃 신호(이 탭 · 다른 탭)에 저장소를 묶습니다. 대화창을 붙이는 공통 레이아웃이 한 번 부르고,
 * 돌려준 함수로 풉니다. 모듈을 읽기만 해도 듣기 시작하지 않게 따로 둡니다
 */
export function bindChatToSession(): () => void {
  return onSignedOut(() => useChatStore.getState().signOut());
}

/** 지금 화면의 표 — 조회 · 목록 요청을 보낼 때 받아 둡니다 */
export function chatTicket(): ChatTicket {
  const { generation, attempt, conversationId, run } = useChatStore.getState();
  return {
    generation,
    attempt,
    conversationId,
    runId: run?.runId ?? null,
    sendId: null,
    scopeChangeId: null,
    createId: null,
  };
}

/** 되살린 보내기를 다시 보낼 때의 표 — 같은 sendId 로 보냅니다. 보내는 중이 아니면 null */
export function pendingSendTicket(): ChatTicket | null {
  const { pending } = useChatStore.getState();
  return pending && { ...chatTicket(), sendId: pending.sendId };
}

/**
 * 조회 · 목록 응답이 아직 지금 화면의 것인지. 세대 · 시도 · 대화 · 실행 중 하나라도 달라졌으면 버립니다 (12-4 F3
 * "응답의 run_id · 대화 ID · 세션 세대가 지금과 다르면 버린다"). 보내기 · 범위 변경 응답은 그 동작이 따로 견줍니다
 */
export function isCurrent(ticket: ChatTicket): boolean {
  const now = chatTicket();
  if (ticket.generation !== now.generation || ticket.attempt !== now.attempt) return false;
  if (ticket.runId !== null) return ticket.runId === now.runId;
  return ticket.conversationId === now.conversationId;
}
