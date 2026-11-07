import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { MemorySettings } from '@/types/chat';
import {
  clearMemoryItems,
  fetchMemory,
  memoryKey,
  removeMemoryItem,
  saveMemoryEnabled,
} from './chatMemory';

/**
 * 기억 설정 화면 · '기억했어요' 카드 · '기억 없이' 스위치가 함께 읽는 서버 값 (12-4 F14 · F15 · F16).
 *
 * - 다른 탭 · 기기에서 바뀐 값은 화면을 다시 열거나 창으로 돌아올 때 다시 읽어 맞춥니다(L3-43 "다른 탭에서 바뀜").
 *   앱 기본값(30초 동안 다시 읽지 않음 · 창 초점에 안 읽음)과 달리 여기서는 매번 다시 읽습니다.
 * - 바꾸는 요청은 모두 바뀐 뒤의 설정 전체를 돌려주므로 받은 값으로 캐시를 바꿉니다. 성공 표시는 그 뒤에만 합니다.
 *   실패하면 캐시를 건드리지 않아 이전 상태가 그대로 보입니다.
 */
export function useMemorySettings(userId: string) {
  return useQuery({
    queryKey: memoryKey(userId),
    queryFn: ({ signal }) => fetchMemory(signal),
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
}

/** 기억을 바꾸는 요청들의 키 — 앞부분이 같아 "무엇이든 바꾸는 중" 을 한 번에 셉니다 */
const changeKey = (userId: string) => [...memoryKey(userId), 'change'] as const;
const removeKey = (userId: string, itemId: string) =>
  [...changeKey(userId), 'remove', itemId] as const;

/** 설정 화면 — 바꾸는 요청이 하나라도 나가 있으면 모든 버튼을 막습니다 */
export const useMemoryChanging = (userId: string) =>
  useIsMutating({ mutationKey: changeKey(userId) }) > 0;

function useSetMemory(userId: string) {
  const queryClient = useQueryClient();
  return useCallback(
    (data: MemorySettings) => queryClient.setQueryData(memoryKey(userId), data),
    [queryClient, userId],
  );
}

/** 코칭 프로필 기억 켜기 · 끄기 */
export function useSaveMemoryEnabled(userId: string) {
  const setMemory = useSetMemory(userId);
  return useMutation({
    mutationKey: [...changeKey(userId), 'enabled'],
    mutationFn: (enabled: boolean) => saveMemoryEnabled(enabled),
    onSuccess: setMemory,
  });
}

/** [전체 지우기] */
export function useClearMemory(userId: string) {
  const setMemory = useSetMemory(userId);
  return useMutation({
    mutationKey: [...changeKey(userId), 'clear'],
    mutationFn: () => clearMemoryItems(),
    onSuccess: setMemory,
  });
}

/**
 * 없는 항목 (BE `ChatTargetNotFound` 404 — 없는 항목과 남의 항목이 같은 404). 다른 까닭의 404 가 섞여도
 * 지운 것으로 본 뒤 목록을 다시 읽으니 화면은 서버 값으로 돌아옵니다
 */
const isGone = (error: unknown) => error instanceof ApiFailure && error.status === 404;

/**
 * 항목 하나 [지우기] — 설정 화면의 줄과 '기억했어요' 카드가 같이 씁니다 (L3-45).
 *
 * - 누르는 동안 그 `item_id` 만 막습니다. 같은 항목의 카드 · 줄이 여럿이어도 함께 막힙니다(키가 같음).
 * - 이미 지운 항목(404)은 지운 것으로 봅니다("이미 지워졌으면 처음 결과로") — 목록에서 빼고 다시 읽습니다.
 * - `onRemoved` 는 서버가 지운 뒤에만 부릅니다(성공 표시).
 */
export function useRemoveMemoryItem(userId: string, itemId: string, onRemoved?: () => void) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: removeKey(userId, itemId),
    mutationFn: async (): Promise<MemorySettings | null> => {
      try {
        return await removeMemoryItem(itemId);
      } catch (error) {
        if (isGone(error)) return null;
        throw error;
      }
    },
    // 지운 줄은 목록에서 빠져 사라지므로, 알림은 줄이 아니라 이 요청에 붙입니다
    onSuccess: (data) => {
      onRemoved?.();
      if (data) {
        queryClient.setQueryData(memoryKey(userId), data);
        return;
      }
      queryClient.setQueryData<MemorySettings>(
        memoryKey(userId),
        (old) => old && { ...old, items: old.items.filter((i) => i.item_id !== itemId) },
      );
      queryClient
        .invalidateQueries({ queryKey: memoryKey(userId), exact: true })
        .catch(() => undefined);
    },
  });
  const busy = useIsMutating({ mutationKey: removeKey(userId, itemId) }) > 0;
  const { mutate } = mutation;
  const remove = useCallback(() => {
    // 화면이 다시 그려지기 전에 또 눌려도(연타) 나가 있는 요청을 직접 세어 막습니다
    if (queryClient.isMutating({ mutationKey: removeKey(userId, itemId) }) > 0) return;
    mutate();
  }, [queryClient, mutate, userId, itemId]);
  return { remove, busy, failed: mutation.isError && !busy };
}
