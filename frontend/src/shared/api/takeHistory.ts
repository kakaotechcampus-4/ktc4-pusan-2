import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { TakeHistoryResponse } from '@/types/takeHistory';

const takeHistoryKey = ['takes', 'history'] as const;

export function fetchTakeHistory(pitchId?: string) {
  const query = pitchId ? `?pitch_id=${encodeURIComponent(pitchId)}` : '';
  return apiRequest<TakeHistoryResponse>(`/api/takes/${query}`);
}

/** pitchId 가 없으면 모든 피치의 Take 입니다 (P16) */
export function useTakeHistory(pitchId?: string) {
  return useQuery({
    queryKey: [...takeHistoryKey, pitchId ?? 'all'],
    queryFn: () => fetchTakeHistory(pitchId),
  });
}

/** `PUT /api/pitches/{pitch_id}/best-take`. **BE 에 아직 없는 API 입니다 (FE 제안)** */
export function useChooseBestTake(pitchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { take_id: string }) =>
      apiRequest<void>(`/api/pitches/${encodeURIComponent(pitchId)}/best-take`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    // 기록 · 비교 화면의 BEST 표시도 같은 목록에서 옵니다
    onSuccess: () => queryClient.invalidateQueries({ queryKey: takeHistoryKey }),
  });
}
