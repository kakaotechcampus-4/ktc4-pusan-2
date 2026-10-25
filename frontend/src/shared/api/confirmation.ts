import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { Confirmations, ConfirmationsRequest, ConfirmationsSent } from '@/types/confirmation';

/** 대본 확인 — BE #528의 REST 응답을 그대로 받습니다 (`types/confirmation.ts`). */

export function useConfirmations(takeId: string) {
  return useQuery({
    queryKey: ['takes', takeId, 'confirmations'],
    queryFn: () => apiRequest<Confirmations>(`/api/takes/${takeId}/confirmations`),
    // 화면에 다시 오면 단계 · 남은 시간을 새로 받습니다. 답을 고르는 중에는 포커스 복귀로 갱신하지 않습니다.
    staleTime: 0,
    refetchOnMount: 'always',
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
