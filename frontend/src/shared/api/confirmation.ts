import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { Confirmations, ConfirmationsRequest, ConfirmationsSent } from '@/types/confirmation';

/** 대본 확인 — 합의 전 FE 제안 API 입니다 (`types/confirmation.ts`) */

export function useConfirmations(takeId: string) {
  return useQuery({
    queryKey: ['takes', takeId, 'confirmations'],
    queryFn: () => apiRequest<Confirmations>(`/api/takes/${takeId}/confirmations`),
    // 남은 시간을 받은 순간부터 세므로 다시 묻지 않습니다 — 다시 받으면 고른 답과 시계가 어긋납니다
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

/** 답 보내기 · 건너뛰기(빈 답). 보내고 나면 분석 대기가 새 단계를 보도록 리포트를 다시 묻게 합니다 */
export function useSendConfirmations(takeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfirmationsRequest) =>
      postJson<ConfirmationsSent>(`/api/takes/${takeId}/confirmations`, body),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['takes', takeId] }),
  });
}
