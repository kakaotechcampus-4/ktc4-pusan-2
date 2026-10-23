import { useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { TakeHistoryResponse } from '@/types/takeHistory';

export function fetchTakeHistory(pitchId?: string) {
  const query = pitchId ? `?pitch_id=${encodeURIComponent(pitchId)}` : '';
  return apiRequest<TakeHistoryResponse>(`/api/takes/${query}`);
}

/** pitchId 가 없으면 모든 피치의 Take 입니다 (P16) */
export function useTakeHistory(pitchId?: string) {
  return useQuery({
    queryKey: ['takes', 'history', pitchId ?? 'all'],
    queryFn: () => fetchTakeHistory(pitchId),
  });
}
