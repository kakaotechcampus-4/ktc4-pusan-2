import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { apiRequest } from '@/shared/api/client';
import { chatPaths } from './chatPaths';
import { ApiFailure } from '@/shared/api/tokenStore';

/**
 * 사용 가능 여부 (`docs/ai/features/chat-agent.md` 12-4 F1 · L3-02) — 버튼을 띄울지 서버(BE 설정)에 묻습니다.
 *
 * - 조회하는 동안은 숨깁니다(`checking`).
 * - 5초 안에 답이 없으면 실패로 세고 2 · 4 · 8초 뒤 최대 3번 다시 묻습니다. 모두 실패하면 버튼은 보이고(`failed`),
 *   열면 "코치를 불러오지 못했으니 다시 시도해 주세요." 와 [다시 시도]를 보입니다.
 * - 명시적인 `enabled:false` 또는 `AGENT_DISABLED` 만 꺼짐(`off`)입니다 — 실패를 꺼짐으로 단정하지 않습니다.
 * - 화면 복귀(창 초점 · 허용 화면으로 돌아옴) · 새 세션(사용자별 키) 때 다시 묻습니다. 다시 묻다 실패해도 앞 답을 지킵니다
 */
export type ChatAvailability = 'checking' | 'on' | 'off' | 'failed';

export const AVAILABILITY_TIMEOUT_MS = 5_000;
export const AVAILABILITY_RETRY_MS = [2_000, 4_000, 8_000] as const;

export const availabilityKey = (userId: string) => ['chat', 'availability', userId] as const;

const isDisabled = (error: unknown) =>
  error instanceof ApiFailure && error.status === 503 && error.code === 'AGENT_DISABLED';

/** 켜짐이면 true, 꺼짐(`enabled:false` · `AGENT_DISABLED`)이면 false. 그 밖(시간 초과 · 끊김 · 모양 틀림)은 던집니다 */
export async function fetchChatAvailability(signal?: AbortSignal): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AVAILABILITY_TIMEOUT_MS);
  const stop = () => controller.abort();
  signal?.addEventListener('abort', stop);
  try {
    const body = await apiRequest<unknown>(chatPaths.availability, { signal: controller.signal });
    const enabled = (body as { enabled?: unknown } | null)?.enabled;
    if (typeof enabled !== 'boolean') throw new Error('사용 가능 여부 응답 모양이 달라요');
    return enabled;
  } catch (error) {
    if (isDisabled(error)) return false;
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', stop);
  }
}

export function useChatAvailability(userId: string): {
  status: ChatAvailability;
  retry: () => void;
  /** 통신 계층이 `AGENT_DISABLED` 를 받았을 때 — 다시 묻기 전까지 꺼짐으로 둡니다 */
  markDisabled: () => void;
} {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: availabilityKey(userId),
    queryFn: ({ signal }) => fetchChatAvailability(signal),
    retry: AVAILABILITY_RETRY_MS.length,
    retryDelay: (failures) => AVAILABILITY_RETRY_MS[failures] ?? AVAILABILITY_RETRY_MS.at(-1)!,
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  const { refetch } = query;
  const retry = useCallback(() => refetch().catch(() => undefined), [refetch]);
  const markDisabled = useCallback(
    () => queryClient.setQueryData(availabilityKey(userId), false),
    [queryClient, userId],
  );
  return { status: statusOf(query.data, query.isError), retry, markDisabled };
}

function statusOf(data: boolean | undefined, failed: boolean): ChatAvailability {
  if (data !== undefined) return data ? 'on' : 'off';
  return failed ? 'failed' : 'checking';
}
