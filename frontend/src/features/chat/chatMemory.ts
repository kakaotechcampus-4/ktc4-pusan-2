import type { MemorySettings, ProfileItem, ProfileKey } from '@/types/chat';
import { chatApi, type ChatApi } from './chatApi';

/**
 * 기억 설정 · 코칭 프로필 (`docs/ai/features/chat-agent.md` 6-14 · 12-3, BE #1002 `GET · PUT /memory`,
 * `DELETE /memory/items` · `/{id}`).
 *
 * 기억은 대화가 아니라 사용자의 설정이라 통신 계층(`chatTransport`)을 거치지 않습니다. 대화 목록처럼 서버 값으로
 * 읽고(TanStack Query, 키는 사용자별), 응답 모양을 여기서 검사합니다 — 사용자가 바뀌면 키가 달라 앞 사람의 항목이
 * 보이지 않습니다. 네 요청 모두 바뀐 뒤의 설정 전체를 돌려주므로, 화면은 받은 값으로 캐시를 바꾸면 됩니다.
 */

export const memoryKey = (userId: string) => ['chat', 'memory', userId] as const;

const PROFILE_KEYS: readonly ProfileKey[] = ['TONE', 'EXPLAIN_STYLE', 'FOCUS_AREA', 'CONTEXT'];

const isText = (v: unknown): v is string => typeof v === 'string' && v !== '';

function readItem(v: unknown): ProfileItem | null {
  if (typeof v !== 'object' || v === null) return null;
  const item = v as Record<string, unknown>;
  if (!isText(item.item_id) || !isText(item.value)) return null;
  if (!PROFILE_KEYS.includes(item.key as ProfileKey)) return null;
  return { item_id: item.item_id, key: item.key as ProfileKey, value: item.value };
}

/**
 * 기억 설정 응답을 읽습니다 — 설정 모양이 아니면 실패(던짐), 깨진 항목 · 모르는 종류의 항목은 뺍니다.
 * 뺀 항목은 보이지 않을 뿐 서버에는 남습니다 — [전체 지우기]는 그것까지 지웁니다
 */
export function readMemorySettings(v: unknown): MemorySettings {
  const body = v as { enabled?: unknown; items?: unknown } | null;
  if (typeof body?.enabled !== 'boolean' || !Array.isArray(body.items))
    throw new Error('기억 설정 응답 모양이 달라요');
  return {
    enabled: body.enabled,
    items: body.items.map(readItem).filter((item): item is ProfileItem => item !== null),
  };
}

/** 서버 호출 — 시험에서는 `api` 를 바꿔 끼웁니다 */
type MemoryApi = Pick<
  ChatApi,
  'getMemory' | 'setMemoryEnabled' | 'deleteMemoryItems' | 'deleteMemoryItem'
>;

export const fetchMemory = async (signal?: AbortSignal, api: MemoryApi = chatApi) =>
  readMemorySettings(await api.getMemory(signal));

export const saveMemoryEnabled = async (enabled: boolean, api: MemoryApi = chatApi) =>
  readMemorySettings(await api.setMemoryEnabled(enabled));

export const clearMemoryItems = async (api: MemoryApi = chatApi) =>
  readMemorySettings(await api.deleteMemoryItems());

export const removeMemoryItem = async (itemId: string, api: MemoryApi = chatApi) =>
  readMemorySettings(await api.deleteMemoryItem(itemId));
