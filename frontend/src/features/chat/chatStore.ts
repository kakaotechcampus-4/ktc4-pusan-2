import { create } from 'zustand';
import { onSignedOut } from '@/shared/lib/sessionSignal';

/**
 * 대화형 코치 에이전트의 화면 상태 (`docs/ai/features/chat-agent.md` 12-4 F1 · F2 · F3).
 *
 * 왜 Zustand인가 — 대화창은 화면을 옮겨도 이어져야 하고(라우팅으로 컴포넌트가 죽습니다), 서버에서 온 값이
 * 아니며(메시지 · 실행 상태는 TanStack Query), 프레임 단위도 아닙니다. frontend/AGENTS.md 상태 배치표의 가운데 칸입니다.
 *
 * 여기 두는 것: 열림 · 보고 있는 대화 · 범위 · 보내는 중 · 지켜보는 실행과 커서 · 세션 세대 · 모달에 가려진 상태.
 * 서버를 부르는 일은 통신 계층이 하고, 이 저장소는 값과 그 값을 바꿔도 되는지만 정합니다.
 *
 * **잠그는 것** (12-4 F2 · F3)
 * - 보내기: 보내는 중(접수 전)과 대기 · 실행 중에는 막고, 승인 대기에서는 허용합니다 — BE 가 앞 확인을 취소하고 받습니다.
 * - 범위 칩: 보내는 중과 실행이 끝나기 전에는 막습니다("실행 중에는 칩을 막는다").
 * - 새 대화 · 대화 고르기: 막지 않습니다. 실행 중이어도 옮길 수 있고, 앞 대화의 늦은 응답은 표(`isCurrent`)로 버립니다
 *   ("다른 대화로 옮긴 뒤 도착한 이전 대화의 늦은 종결 응답은 무시").
 *
 * 새로고침해도 대화를 이어 가도록(12-4 "복원") 대화 ID · 범위 · 실행 ID 를 sessionStorage 에 사용자별로 둡니다.
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
 * 응답이 지금 화면의 것인지 가릴 표 — 요청을 보낼 때 받아 두고, 응답이 오면 `isCurrent` 로 견줍니다.
 * `attempt` 는 새 대화 · 대화 고르기 · 사용자 바뀜마다 올라서, 대화 ID 가 아직 없는 새 대화의 보내기도 가립니다
 */
export interface ChatTicket {
  generation: number;
  attempt: number;
  conversationId: string | null;
  runId: string | null;
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
  /** 범위를 정했는지 — 칩으로 고르거나 대화를 시작하면, 화면을 옮겨도 덮어쓰지 않습니다 */
  scopeFixed: boolean;
  /** 새 대화를 '기억 없이' 시작할지. 대화를 만든 뒤에는 바꾸지 않습니다 */
  memoryOff: boolean;
  /** 보내기를 시작했고 접수(202)가 아직 오지 않음 */
  sending: boolean;
  run: ActiveRun | null;
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
  /** 새 대화 입력 상태로. 지켜보던 실행은 놓습니다 — 앞 대화의 늦은 응답은 표로 버립니다 */
  startNewConversation: (screenScope: ScreenScope) => void;
  /** 대화 목록에서 지난 대화를 고릅니다. 지켜보던 실행은 놓습니다 */
  selectConversation: (conversationId: string, scope: ChatScope) => void;
  /** 범위 칩으로 범위를 바꿉니다. 보내는 중 · 실행이 끝나기 전에는 막습니다 — 바꾸지 않고 false */
  chooseScope: (scope: ChatScope) => boolean;
  /**
   * 서버가 확정한 범위로 칩을 바꿉니다 — 에이전트가 다른 피치를 연 뒤 BE 가 검증된 `final` 로 확정한 때(12-4 F2).
   * 그 대화를 보고 있을 때만 바꿉니다
   */
  applyServerScope: (conversationId: string, scope: ChatScope) => void;
  /** 새 대화의 '기억 없이'. 이미 만든 대화에서는 바꾸지 않고 false */
  setMemoryOff: (memoryOff: boolean) => boolean;
  /**
   * 보내기를 시작합니다 — 누르는 즉시 잠급니다("전송을 시작하면 바로 잠그고"). 보낼 수 없으면 null:
   * 범위 확인 중 · 이미 보내는 중 · 대기 · 실행 중. 승인 대기는 보낼 수 있습니다
   */
  beginSend: () => ChatTicket | null;
  /** 보내기가 접수되지 않고 끝났을 때(오류 · 거절). 그 표가 지금 것일 때만 잠금을 풉니다 */
  failSend: (ticket: ChatTicket) => void;
  /** 접수(202)를 받았을 때. 표가 지금 것일 때만 붙이고 true — 새 대화였으면 서버가 준 대화 ID 를 이때 받습니다 */
  beginRun: (ticket: ChatTicket, runId: string, conversationId: string) => boolean;
  /** 실행 조회로 단계가 바뀌었을 때. 다른 실행이면 무시합니다 */
  setRunPhase: (runId: string, phase: RunPhase) => void;
  /** 처리한 이벤트 순번까지 커서를 옮깁니다. 다른 실행이거나 뒤로 가는 값이면 무시하고 false */
  advanceCursor: (runId: string, seq: number) => boolean;
  /** 실행이 종결됐을 때. 다른 실행이면 무시합니다 */
  endRun: (runId: string) => void;
  /** 다른 모달이 열리고 닫힐 때 — 모두 닫히면 가려진 동안 정해진 열림 상태로 돌립니다 */
  coverByModal: () => void;
  uncoverByModal: () => void;
}

// ── 새로고침 뒤 복원용 사본 ───────────────────────────────────

const STORAGE_PREFIX = 'pitchcoach.chat.';
const storageKey = (userId: string) => `${STORAGE_PREFIX}${userId}`;

/** 남기는 것 — 커서 · 단계는 남기지 않습니다(되살리면 처음부터 다시 받고, 단계는 조회로 압니다) */
interface Saved {
  conversationId: string | null;
  scope: ScreenScope;
  scopeFixed: boolean;
  run: { runId: string; conversationId: string } | null;
}

function readSaved(userId: string): Saved | null {
  try {
    const raw = sessionStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<Saved> | null;
    if (typeof saved !== 'object' || saved === null) return null;
    return {
      conversationId: typeof saved.conversationId === 'string' ? saved.conversationId : null,
      scope: isScope(saved.scope) ? saved.scope : null,
      scopeFixed: saved.scopeFixed === true,
      run: isSavedRun(saved.run) ? saved.run : null,
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

const isScope = (v: unknown): v is ChatScope =>
  typeof v === 'object' &&
  v !== null &&
  ((v as ChatScope).pitchId === null || typeof (v as ChatScope).pitchId === 'string') &&
  ((v as ChatScope).title === null || typeof (v as ChatScope).title === 'string');

const isSavedRun = (v: unknown): v is NonNullable<Saved['run']> =>
  typeof v === 'object' &&
  v !== null &&
  typeof (v as ActiveRun).runId === 'string' &&
  typeof (v as ActiveRun).conversationId === 'string';

// ── 저장소 ───────────────────────────────────────────────────

const empty = {
  open: false,
  conversationId: null,
  scope: null,
  scopeFixed: false,
  memoryOff: false,
  sending: false,
  run: null,
  coveredBy: 0,
  openAfterCover: false,
} satisfies Partial<ChatState>;

export const useChatStore = create<ChatState>()((set, get) => {
  /** 바뀐 뒤 복원용 사본을 남깁니다 */
  const save = () => {
    const { userId, conversationId, scope, scopeFixed, run } = get();
    if (!userId) return;
    writeSaved(userId, {
      conversationId,
      scope,
      scopeFixed,
      run: run ? { runId: run.runId, conversationId: run.conversationId } : null,
    });
  };
  /** 범위 칩을 막을 때 — 보내는 중이거나 실행이 끝나기 전 */
  const busy = () => get().sending || get().run !== null;
  /** 열림을 바꿉니다 — 모달에 가려진 동안에는 모달이 닫힌 뒤의 상태만 바꿉니다 */
  const setOpen = (open: boolean) => set(get().coveredBy > 0 ? { openAfterCover: open } : { open });

  return {
    userId: null,
    generation: 0,
    attempt: 0,
    ...empty,

    setUser: (userId) => {
      if (userId === get().userId) return;
      dropOthers(userId);
      const saved = userId ? readSaved(userId) : null;
      set((s) => ({
        ...empty,
        userId,
        generation: s.generation + 1,
        attempt: s.attempt + 1,
        ...(saved
          ? {
              conversationId: saved.conversationId,
              scope: saved.scope,
              scopeFixed: saved.scopeFixed,
              // 단계는 모르니 끝나기 전으로 둡니다 — 통신 계층의 첫 조회가 맞춥니다
              run: saved.run && { ...saved.run, phase: 'RUNNING' as const, afterSeq: 0 },
            }
          : {}),
      }));
    },

    signOut: () => {
      dropOthers(null);
      set((s) => ({
        ...empty,
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
        conversationId: null,
        scope: screenScope,
        scopeFixed: false,
        memoryOff: false,
        sending: false,
        run: null,
        attempt: s.attempt + 1,
      }));
      save();
    },

    selectConversation: (conversationId, scope) => {
      set((s) => ({
        conversationId,
        scope,
        scopeFixed: true,
        memoryOff: false,
        sending: false,
        run: null,
        attempt: s.attempt + 1,
      }));
      save();
    },

    chooseScope: (scope) => {
      if (busy()) return false;
      set({ scope, scopeFixed: true });
      save();
      return true;
    },

    applyServerScope: (conversationId, scope) => {
      if (get().conversationId !== conversationId) return;
      set({ scope, scopeFixed: true });
      save();
    },

    setMemoryOff: (memoryOff) => {
      if (get().conversationId !== null) return false;
      set({ memoryOff });
      return true;
    },

    beginSend: () => {
      const { scope, sending, run } = get();
      if (scope === null || sending) return null;
      if (run && run.phase !== 'WAITING_APPROVAL') return null;
      set({ sending: true });
      return chatTicket();
    },

    failSend: (ticket) => {
      if (isCurrent({ ...ticket, runId: null })) set({ sending: false });
    },

    beginRun: (ticket, runId, conversationId) => {
      if (!isCurrent({ ...ticket, runId: null })) return false;
      set({
        sending: false,
        // 보낸 대화가 이 대화가 됩니다 — 새 대화였으면 범위도 이때 정해집니다
        conversationId,
        scopeFixed: true,
        run: { runId, conversationId, phase: 'QUEUED', afterSeq: 0 },
      });
      save();
      return true;
    },

    setRunPhase: (runId, phase) => {
      const { run } = get();
      if (run?.runId !== runId || run.phase === phase) return;
      set({ run: { ...run, phase } });
    },

    advanceCursor: (runId, seq) => {
      const { run } = get();
      if (!run || run.runId !== runId || !Number.isInteger(seq) || seq <= run.afterSeq)
        return false;
      set({ run: { ...run, afterSeq: seq } });
      return true;
    },

    endRun: (runId) => {
      if (get().run?.runId !== runId) return;
      set({ run: null });
      save();
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

/** 지금 화면의 표 — 요청을 보낼 때 받아 둡니다 */
export function chatTicket(): ChatTicket {
  const { generation, attempt, conversationId, run } = useChatStore.getState();
  return { generation, attempt, conversationId, runId: run?.runId ?? null };
}

/**
 * 응답이 아직 지금 화면의 것인지. 세대 · 시도 · 대화 · 실행 중 하나라도 달라졌으면 버립니다 (12-4 F3
 * "응답의 run_id · 대화 ID · 세션 세대가 지금과 다르면 버린다")
 */
export function isCurrent(ticket: ChatTicket): boolean {
  const now = chatTicket();
  if (ticket.generation !== now.generation || ticket.attempt !== now.attempt) return false;
  if (ticket.runId !== null) return ticket.runId === now.runId;
  return ticket.conversationId === now.conversationId;
}
