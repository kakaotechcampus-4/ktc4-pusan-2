import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/shared/api/client';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { TakeHistoryResponse } from '@/types/takeHistory';

export function fetchTakeHistory(pitchId?: string) {
  const query = pitchId ? `?pitch_id=${encodeURIComponent(pitchId)}` : '';
  return apiRequest<TakeHistoryResponse>(`/api/takes/${query}`);
}

/**
 * 없거나 남의 피치(404, BE #398)와 UUID 꼴이 아닌 피치 id(422, 잘린 주소). `pitch_id` 를 줄 때만 옵니다.
 * 코드까지 봐서 프록시가 낸 404 같은 다른 실패는 섞이지 않습니다
 */
export const isPitchMissing = (error: unknown) =>
  error instanceof ApiFailure &&
  ((error.status === 404 && error.code === 'PITCH_NOT_FOUND') ||
    (error.status === 422 && error.code === 'VALIDATION_ERROR'));

/** 기본처럼 한 번 더 묻되, 찾을 수 없는 피치는 다시 물어도 같으니 묻지 않습니다 */
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
