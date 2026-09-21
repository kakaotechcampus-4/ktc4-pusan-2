import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { CompleteRequest, PitchDetail, TakeContext } from '@/types/api';

export const takeKey = (takeId: string) => ['take', takeId] as const;
export const pitchKey = (pitchId: string) => ['pitch', pitchId] as const;

/** 리허설 화면이 takeId 하나로 들어와도 서게 하는 값 — 새로고침 복귀에도 씁니다 */
export function useTakeContext(takeId: string) {
  return useQuery({
    queryKey: takeKey(takeId),
    queryFn: () => apiRequest<TakeContext>(`/api/takes/${takeId}`),
    enabled: takeId !== '',
    staleTime: Infinity,
  });
}

/**
 * 발표 자료와 대본. **시작 전에 한 번에 다 받아 둡니다** —
 * 발표 중에 네트워크를 타면 그 순간 화면이 빕니다 (CLAUDE.md 4번).
 * 그래서 staleTime을 무한으로 둡니다. 발표 중에 다시 받아오지 않습니다.
 */
export function usePitchDetail(pitchId: string | undefined) {
  return useQuery({
    queryKey: pitchKey(pitchId ?? ''),
    queryFn: () => apiRequest<PitchDetail>(`/api/pitches/${pitchId}`),
    enabled: Boolean(pitchId),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/**
 * 발표 종료. 실패하면 P15(재시도)로 보냅니다 —
 * 기록은 브라우저에 남아 있으므로 여기서 잃는 것은 없습니다.
 * `clientSessionId`가 멱등키라 같은 값으로 다시 보내도 Take가 늘지 않습니다.
 */
export function useCompleteTake(takeId: string) {
  return useMutation({
    mutationFn: (body: CompleteRequest) =>
      postJson<{ takeId: string; status: string }>(`/api/takes/${takeId}/complete`, body),
  });
}
