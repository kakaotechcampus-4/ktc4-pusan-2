import { canChangeScope, type useChatStore } from '../store/chatStore';

type ChatState = ReturnType<typeof useChatStore.getState>;

/**
 * 잠긴 버튼의 이유 (12-4 "키보드 · 접근성" — "모든 카드에 이름과 비활성 이유를 둔다").
 * 화면 읽기 프로그램이 버튼 이름과 함께 읽게 버튼 안에 숨겨 둡니다. 문구는 FE 가 정했습니다
 */
export function sendLockReason(s: ChatState, scopeBlocked: boolean): string {
  if (scopeBlocked) return '범위를 다시 고른 뒤 누를 수 있어요';
  if (s.scope === null) return '범위를 확인하는 중이에요';
  if (s.pending || s.creating) return '보내는 중이에요';
  if (s.cancelling) return '취소를 확인하는 중이에요';
  if (s.run) return '답을 만드는 중이에요';
  if (s.syncing || s.scopeChange) return '대화 상태를 확인하는 중이에요';
  if (s.blockedUntil !== null) return '잠시 뒤에 누를 수 있어요';
  return '지금은 누를 수 없어요';
}

/** 범위 칩이 잠긴 이유 */
export function scopeLockReason(s: ChatState): string | null {
  // 범위를 모를 때는 칩 글자("범위 확인 중")가 이미 이유입니다
  if (s.scope === null || canChangeScope(s)) return null;
  if (s.run || s.pending || s.creating || s.cancelling)
    return '답을 만드는 동안에는 범위를 바꿀 수 없어요';
  return '지금은 범위를 바꿀 수 없어요';
}

/** 앞에서부터 처음 맞는 이유 — 맞는 것이 없으면 null */
export function pickReason(...entries: [boolean, string][]): string | null {
  return entries.find(([when]) => when)?.[1] ?? null;
}
