import { create } from 'zustand';
import { onSignedOut } from '@/shared/lib/sessionSignal';

/**
 * 대화형 코치 에이전트의 화면 상태 (`docs/ai/features/chat-agent.md` 12-4 F1 · F2 · F3).
 *
 * 왜 Zustand인가 — 대화창은 화면을 옮겨도 이어져야 하고(라우팅으로 컴포넌트가 죽습니다), 서버에서 온 값이
 * 아니며(메시지 · 실행 상태는 TanStack Query), 프레임 단위도 아닙니다. frontend/AGENTS.md 상태 배치표의 가운데 칸입니다.
 *
 * 여기 두는 것: 열림 · 보고 있는 대화 · 범위 · 실행 중인 실행과 커서 · 세션 세대 · 모달에 가려진 상태.
 * 서버를 부르는 일은 통신 계층이 하고, 이 저장소는 값과 그 값을 바꿔도 되는지만 정합니다.
 *
 * 새로고침해도 대화를 이어 가도록(12-4 "복원") 대화 ID · 범위 · 실행 ID 를 sessionStorage 에 사용자별로 둡니다.
 * 다른 사용자의 사본은 그 사용자가 아니게 되는 순간 지웁니다("로컬 임시 ID 는 사용자별로 나누고 만료 때 버린다").
 */

/** 대화 범위 — 전체(null) 또는 피치 하나. 칩에 보일 이름을 함께 둡니다 */
export interface ChatScope {
  pitchId: string | null;
  title: string | null;
}

export const WHOLE_SCOPE: ChatScope = { pitchId: null, title: null };

/** 답을 만드는 중인 실행. 통신 계층이 이것으로 조회하고, 커서는 앞으로만 갑니다 */
export interface ActiveRun {
  runId: string;
  conversationId: string;
  /** 마지막으로 처리한 이벤트 순번(`after_seq`). 같은 순번은 한 번만 처리합니다 */
  afterSeq: number;
}

/** 응답이 지금 화면의 것인지 가릴 표 — 요청을 보낼 때 받아 두고, 응답이 오면 `isCurrent` 로 견줍니다 */
export interface ChatTicket {
  generation: number;
  conversationId: string | null;
  runId: string | null;
}

interface ChatState {
  /** 지금 로그인한 사용자. 모르면 null */
  userId: string | null;
  /** 세션 세대 — 사용자가 바뀌거나 로그아웃하면 올라가고, 앞 세대의 응답은 버립니다 */
  generation: number;
  open: boolean;
  /** 보고 있는 대화. 새 대화 입력 상태면 null */
  conversationId: string | null;
  scope: ChatScope;
  /** 사용자가 범위 칩으로 직접 골랐는지 — 고른 범위는 화면을 옮겨도 덮어쓰지 않습니다 */
  scopeChosen: boolean;
  /** 새 대화를 '기억 없이' 시작할지. 대화를 만든 뒤에는 바꾸지 않습니다 */
  memoryOff: boolean;
  run: ActiveRun | null;
  /** 다른 모달이 대화창을 가렸는지와, 가리기 전에 열려 있었는지 */
  coveredByModal: boolean;
  openBeforeModal: boolean;

  /** 로그인 사용자가 정해지거나 바뀔 때. 다른 사용자면 비우고 세대를 올린 뒤, 그 사용자의 사본을 되살립니다 */
  setUser: (userId: string | null) => void;
  /** 로그아웃(이 탭 · 다른 탭) — 비우고 세대를 올립니다 */
  signOut: () => void;
  /**
   * 대화창을 엽니다. 아직 대화를 시작하지 않았고 범위를 직접 고르지 않았으면, 연 화면의 범위로 맞춥니다
   * (12-4 "처음 범위는 대화창을 연 화면으로 정한다" · "화면을 옮겨도 열린 대화의 범위는 바뀌지 않는다")
   */
  openChat: (screenScope: ChatScope) => void;
  closeChat: () => void;
  /** 새 대화 입력 상태로. 실행 중이면 바꾸지 않고 false */
  startNewConversation: (screenScope: ChatScope) => boolean;
  /** 대화 목록에서 지난 대화를 고릅니다. 실행 중이면 바꾸지 않고 false */
  selectConversation: (conversationId: string, scope: ChatScope) => boolean;
  /** 범위 칩으로 범위를 바꿉니다. 실행 중에는 칩을 막습니다(12-4 F2) — 바꾸지 않고 false */
  chooseScope: (scope: ChatScope) => boolean;
  /**
   * 서버가 확정한 범위로 칩을 바꿉니다 — 에이전트가 다른 피치를 연 뒤 BE 가 검증된 `final` 로 확정한 때(12-4 F2).
   * 그 대화를 보고 있을 때만 바꿉니다
   */
  applyServerScope: (conversationId: string, scope: ChatScope) => void;
  /** 새 대화의 '기억 없이'. 이미 만든 대화에서는 바꾸지 않고 false */
  setMemoryOff: (memoryOff: boolean) => boolean;
  /** 보내기가 접수됐을 때(202). 새 대화였으면 서버가 준 대화 ID 를 이때 받습니다 */
  beginRun: (runId: string, conversationId: string) => void;
  /** 처리한 이벤트 순번까지 커서를 옮깁니다. 다른 실행이거나 뒤로 가는 값이면 무시하고 false */
  advanceCursor: (runId: string, seq: number) => boolean;
  /** 실행이 종결됐을 때. 다른 실행이면 무시합니다 */
  endRun: (runId: string) => void;
  /** 다른 모달이 열리고 닫힐 때 — 닫히면 가리기 전의 열림 상태로 돌립니다 */
  coverByModal: () => void;
  uncoverByModal: () => void;
}

// ── 새로고침 뒤 복원용 사본 ───────────────────────────────────

const STORAGE_PREFIX = 'pitchcoach.chat.';
const storageKey = (userId: string) => `${STORAGE_PREFIX}${userId}`;

interface Saved {
  conversationId: string | null;
  scope: ChatScope;
  scopeChosen: boolean;
  run: ActiveRun | null;
}

function readSaved(userId: string): Saved | null {
  try {
    const raw = sessionStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<Saved>;
    if (typeof saved !== 'object' || saved === null) return null;
    return {
      conversationId: typeof saved.conversationId === 'string' ? saved.conversationId : null,
      scope: isScope(saved.scope) ? saved.scope : WHOLE_SCOPE,
      scopeChosen: saved.scopeChosen === true,
      run: isRun(saved.run) ? saved.run : null,
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

/** 이 사용자 말고 다른 사용자의 사본을 지웁니다 */
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

const isRun = (v: unknown): v is ActiveRun =>
  typeof v === 'object' &&
  v !== null &&
  typeof (v as ActiveRun).runId === 'string' &&
  typeof (v as ActiveRun).conversationId === 'string' &&
  Number.isInteger((v as ActiveRun).afterSeq) &&
  (v as ActiveRun).afterSeq >= 0;

// ── 저장소 ───────────────────────────────────────────────────

const empty = {
  open: false,
  conversationId: null,
  scope: WHOLE_SCOPE,
  scopeChosen: false,
  memoryOff: false,
  run: null,
  coveredByModal: false,
  openBeforeModal: false,
} satisfies Partial<ChatState>;

export const useChatStore = create<ChatState>()((set, get) => {
  /** 바뀐 뒤 복원용 사본을 남깁니다 */
  const save = () => {
    const { userId, conversationId, scope, scopeChosen, run } = get();
    if (userId) writeSaved(userId, { conversationId, scope, scopeChosen, run });
  };
  const running = () => get().run !== null;

  return {
    userId: null,
    generation: 0,
    ...empty,

    setUser: (userId) => {
      if (userId === get().userId) return;
      dropOthers(userId);
      const saved = userId ? readSaved(userId) : null;
      set((s) => ({
        ...empty,
        userId,
        generation: s.generation + 1,
        ...(saved ?? {}),
      }));
    },

    signOut: () => {
      dropOthers(null);
      set((s) => ({ ...empty, userId: null, generation: s.generation + 1 }));
    },

    openChat: (screenScope) => {
      const { conversationId, scopeChosen, run } = get();
      const fresh = conversationId === null && run === null && !scopeChosen;
      set({ open: true, ...(fresh ? { scope: screenScope } : {}) });
      save();
    },

    closeChat: () => set({ open: false }),

    startNewConversation: (screenScope) => {
      if (running()) return false;
      set({ conversationId: null, scope: screenScope, scopeChosen: false, memoryOff: false });
      save();
      return true;
    },

    selectConversation: (conversationId, scope) => {
      if (running()) return false;
      set({ conversationId, scope, scopeChosen: true, memoryOff: false });
      save();
      return true;
    },

    chooseScope: (scope) => {
      if (running()) return false;
      set({ scope, scopeChosen: true });
      save();
      return true;
    },

    applyServerScope: (conversationId, scope) => {
      if (get().conversationId !== conversationId) return;
      set({ scope, scopeChosen: true });
      save();
    },

    setMemoryOff: (memoryOff) => {
      if (get().conversationId !== null) return false;
      set({ memoryOff });
      return true;
    },

    beginRun: (runId, conversationId) => {
      set({ run: { runId, conversationId, afterSeq: 0 }, conversationId });
      save();
    },

    advanceCursor: (runId, seq) => {
      const { run } = get();
      if (!run || run.runId !== runId || !Number.isInteger(seq) || seq <= run.afterSeq)
        return false;
      set({ run: { ...run, afterSeq: seq } });
      save();
      return true;
    },

    endRun: (runId) => {
      if (get().run?.runId !== runId) return;
      set({ run: null });
      save();
    },

    coverByModal: () => {
      if (get().coveredByModal) return;
      set((s) => ({ coveredByModal: true, openBeforeModal: s.open, open: false }));
    },

    uncoverByModal: () => {
      if (!get().coveredByModal) return;
      set((s) => ({ coveredByModal: false, open: s.openBeforeModal, openBeforeModal: false }));
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
  const { generation, conversationId, run } = useChatStore.getState();
  return { generation, conversationId, runId: run?.runId ?? null };
}

/**
 * 응답이 아직 지금 화면의 것인지. 세대 · 대화 · 실행 중 하나라도 달라졌으면 버립니다 (12-4 F3
 * "응답의 run_id · 대화 ID · 세션 세대가 지금과 다르면 버린다")
 */
export function isCurrent(ticket: ChatTicket): boolean {
  return ticket.generation === useChatStore.getState().generation && sameTarget(ticket);
}

function sameTarget(ticket: ChatTicket) {
  const now = chatTicket();
  if (ticket.runId !== null) return ticket.runId === now.runId;
  return ticket.conversationId === now.conversationId;
}
