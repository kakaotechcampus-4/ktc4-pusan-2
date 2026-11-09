import type { ConversationList, ConversationListItem } from '@/types/chat';
import { chatApi } from './chatApi';
import type { ScreenScope } from '../store/chatStore';

/** 칩 글자 — "범위 확인 중" · "전체" · "피치: 발표 이름". 이름을 모르면 `titleOf` 로 찾고, 그래도 없으면 "피치" */
export function scopeLabel(scope: ScreenScope, titleOf?: (pitchId: string) => string | null) {
  if (scope === null) return '범위 확인 중';
  if (scope.pitchId === null) return '전체';
  const title = scope.title ?? titleOf?.(scope.pitchId) ?? null;
  return title ? `피치: ${title}` : '피치';
}

/**
 * 대화 목록 (`docs/ai/features/chat-agent.md` 12-4 F2 "대화 고르기", BE `GET /api/chat/conversations`).
 * 목록은 서버 값이라 TanStack Query 로 읽고, 한 줄씩 모양을 검사합니다 — 깨진 줄은 빼고 나머지는 보입니다.
 */

export const conversationsKey = (userId: string) => ['chat', 'conversations', userId] as const;

const isText = (v: unknown): v is string => typeof v === 'string' && v !== '';

function readItem(v: unknown): ConversationListItem | null {
  if (typeof v !== 'object' || v === null) return null;
  const c = v as Record<string, unknown>;
  const ok =
    isText(c.conversation_id) &&
    typeof c.title === 'string' &&
    (c.scope_pitch_id === null || isText(c.scope_pitch_id)) &&
    (c.scope_pitch_title === null ||
      c.scope_pitch_title === undefined ||
      typeof c.scope_pitch_title === 'string') &&
    typeof c.last_user_activity_at === 'string';
  if (!ok) return null;
  return {
    conversation_id: c.conversation_id as string,
    title: c.title as string,
    scope_pitch_id: c.scope_pitch_id as string | null,
    scope_pitch_title: (c.scope_pitch_title as string | null | undefined) ?? null,
    last_user_activity_at: c.last_user_activity_at as string,
    created_at:
      typeof c.created_at === 'string' ? c.created_at : (c.last_user_activity_at as string),
    // '기억 없이' 표시 — 칸이 없거나 깨졌으면 기억을 쓰는 대화로 봅니다(BE 기본값)
    memory_off: c.memory_off === true,
    stale: c.stale === true,
  };
}

/** 목록 응답을 읽습니다 — 목록 모양이 아니면 실패(던짐), 깨진 줄은 뺍니다 */
export function readConversationList(v: unknown): ConversationListItem[] {
  const items = (v as { conversations?: unknown } | null)?.conversations;
  if (!Array.isArray(items)) throw new Error('대화 목록 응답 모양이 달라요');
  return items.map(readItem).filter((c): c is ConversationListItem => c !== null);
}

/**
 * 목록 한 쪽을 읽습니다. 다음 커서가 없거나 깨졌으면(빈 글자 · 글자가 아님) 끝으로 봅니다 —
 * 깨진 커서로 다시 부르면 BE 가 422 를 줘서, 더 보기를 눌러도 실패만 되풀이됩니다
 */
export function readConversationPage(v: unknown): ConversationList {
  const cursor = (v as { next_cursor?: unknown } | null)?.next_cursor;
  return { conversations: readConversationList(v), next_cursor: isText(cursor) ? cursor : null };
}

export async function fetchConversationPage(cursor: string | null, signal?: AbortSignal) {
  return readConversationPage(await chatApi.listConversations(signal, cursor));
}

/**
 * 쪽들을 한 목록으로 — 쪽을 읽는 사이 활동이 생겨 같은 대화가 두 쪽에 오면 먼저(최근) 온 것만 둡니다
 */
export function joinConversationPages(pages: readonly ConversationList[]): ConversationListItem[] {
  const seen = new Set<string>();
  return pages
    .flatMap((page) => page.conversations)
    .filter((c) => !seen.has(c.conversation_id) && seen.add(c.conversation_id));
}

/** 목록의 마지막 시각 — "11. 6. 오후 9:30" */
export function lastActivityText(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  return at.toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}
