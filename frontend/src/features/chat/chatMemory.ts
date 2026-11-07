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

/** 코칭 프로필 항목 이름 (문서 5-2 `ProfileItem`) — 카드와 설정 화면이 같이 씁니다 */
export const PROFILE_KEY_LABEL: Record<ProfileKey, string> = {
  TONE: '답 말투 · 길이',
  EXPLAIN_STYLE: '설명 방식',
  FOCUS_AREA: '먼저 보고 싶은 영역',
  CONTEXT: '발표 상황',
};

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
 * 뺀 항목은 보이지 않을 뿐 서버에는 남습니다 — [전체 지우기]는 그것까지 지웁니다.
 * 빼면 BE 모양이 바뀐 것(새 키 등)을 알아채게 개수만 남깁니다 — 값은 사용자 정보라 남기지 않습니다
 */
export function readMemorySettings(v: unknown): MemorySettings {
  const body = v as { enabled?: unknown; items?: unknown } | null;
  if (typeof body?.enabled !== 'boolean' || !Array.isArray(body.items))
    throw new Error('기억 설정 응답 모양이 달라요');
  const items = body.items.map(readItem).filter((item): item is ProfileItem => item !== null);
  const dropped = body.items.length - items.length;
  if (dropped > 0) console.warn('기억 항목이 계약 모양이 아니라 뺐어요', { dropped });
  return { enabled: body.enabled, items };
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

/**
 * 고정 문구 (문서 7-10 "코칭 프로필 기억", L3-43) — 첫 안내 한 줄과 설정 화면 설명에 그대로 씁니다
 */
export const MEMORY_INTRO_TEXT =
  "코칭 프로필 기억 — 말투 · 설명 방식처럼 직접 기억해 달라고 하신 것만 저장해요. 설정에서 끄거나 지울 수 있어요. 끄거나 '기억 없이' 대화를 골라도 그 대화의 기록 · 요약은 보관되고, 다른 대화의 맞춤 답에만 쓰지 않아요.";

/** 기억 설정 화면 문구 — FE 가 정한 문구 — 문서에 없습니다 */
export const MEMORY_TEXT = {
  loading: '기억 설정을 불러오고 있어요.',
  loadFailed: '기억 설정을 불러오지 못했으니 다시 시도해 주세요.',
  empty: '저장된 기억이 없어요.',
  enabledOn: '코칭 프로필 기억을 켰어요.',
  enabledOff: '코칭 프로필 기억을 껐어요.',
  enabledFailed: '설정을 바꾸지 못했으니 다시 시도해 주세요.',
  cleared: '기억을 모두 지웠어요.',
  clearConfirm: '정말 모두 지울까요? 지운 기억은 되돌릴 수 없어요.',
  clearFailed: '기억을 지우지 못했으니 다시 시도해 주세요.',
  removed: '기억을 지웠어요.',
} as const;

/**
 * '기억했어요' 카드의 항목이 지금 어떤지 (L3-45) — 서버의 기억 목록과 맞춰 봅니다.
 *
 * - `unknown`: 목록을 아직 못 읽음 — [지우기]를 둡니다. 이미 지운 항목이면 서버가 404 로 알려 줍니다
 * - `kept`: 목록에 같은 `item_id` · 같은 값이 있음 — [지우기]를 둡니다
 * - `changed`: 같은 `item_id` 에 다른 값 — BE 는 같은 종류(key)를 다시 기억하면 `item_id` 는 두고 값만 바꿉니다
 *   (`upsert_profile_item`, L2-47). 지난 카드로 지금 값을 지우지 않게 [지우기]를 끕니다
 * - `removed`: 목록에 그 `item_id` 가 없음 — 지운 항목, [지우기]를 끕니다
 */
export type MemoryItemState = 'unknown' | 'kept' | 'changed' | 'removed';

export function memoryItemState(
  settings: MemorySettings | undefined,
  itemId: string,
  value: string,
): MemoryItemState {
  if (!settings) return 'unknown';
  const item = settings.items.find((i) => i.item_id === itemId);
  if (!item) return 'removed';
  return item.value === value ? 'kept' : 'changed';
}

/**
 * 첫 안내를 보였는지 — 사용자별로 이 브라우저(localStorage)에 둡니다. 다른 기기에서는 한 번 더 보일 수 있습니다.
 * 문서(12-3 "기억 설정")는 BE 가 사용자별로 저장한다고 하지만 BE 응답에 그 칸이 아직 없습니다 (BE 미구현)
 */
const introKey = (userId: string) => `pitchcoach.chatMemoryIntro.${userId}`;

/** 저장소를 못 쓰면(막힌 저장소 등) 안 본 것으로 칩니다 — 못 보고 지나가는 것(L3-43 "첫 안내를 못 봄")보다 낫습니다 */
export function memoryIntroSeen(userId: string): boolean {
  try {
    return localStorage.getItem(introKey(userId)) !== null;
  } catch {
    return false;
  }
}

export function markMemoryIntroSeen(userId: string): void {
  try {
    localStorage.setItem(introKey(userId), '1');
  } catch {
    // 못 적으면 다음에 한 번 더 보일 뿐입니다
  }
}
