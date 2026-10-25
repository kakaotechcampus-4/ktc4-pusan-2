import { useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import { ApiFailure } from './tokenStore';
import type { TakeHistoryResponse } from '@/types/takeHistory';

export function fetchTakeHistory(pitchId?: string) {
  const query = pitchId ? `?pitch_id=${encodeURIComponent(pitchId)}` : '';
  return apiRequest<TakeHistoryResponse>(`/api/takes/${query}`);
}

/** 없거나 남의 피치 (BE #398). `pitch_id` 를 줄 때만 옵니다 */
export const isPitchMissing = (error: unknown) =>
  error instanceof ApiFailure && error.status === 404;

/** 기본처럼 한 번 더 묻되, 404 는 다시 물어도 같으니 묻지 않습니다 */
export const retryTakeHistory = (failureCount: number, error: Error) =>
  failureCount < 1 && !isPitchMissing(error);

/** pitchId 가 없으면 모든 피치의 Take 입니다 (P16) */
export function useTakeHistory(pitchId?: string) {
  return useQuery({
    queryKey: ['takes', 'history', pitchId ?? 'all'],
    queryFn: () => fetchTakeHistory(pitchId),
    retry: retryTakeHistory,
  });
}
