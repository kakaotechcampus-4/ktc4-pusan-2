import { useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { TakeReport } from '@/types/report';

/** 분석이 끝나기 전에는 이 간격으로 다시 묻습니다 */
const POLL_MS = 2000;

export function fetchTakeReport(takeId: string) {
  return apiRequest<TakeReport>(`/api/takes/${takeId}/report`);
}

/**
 * 더 기다려도 바뀌지 않는 상태인가. 분석에 실패했거나, 리뷰가 끝났거나(실패 · 건너뜀 포함), 단계가 DONE 이면 그렇습니다.
 * 리뷰가 실패해도 측정값은 남아 있어 리포트로 넘어갑니다.
 */
export function isSettled(report: TakeReport | undefined): boolean {
  if (!report) return false;
  return (
    report.status === 'FAILED' || report.review_status !== null || report.analysis_stage === 'DONE'
  );
}

export function useTakeReport(takeId: string) {
  return useQuery({
    queryKey: ['takes', takeId, 'report'],
    queryFn: () => fetchTakeReport(takeId),
    refetchInterval: (query) => (isSettled(query.state.data) ? false : POLL_MS),
  });
}
