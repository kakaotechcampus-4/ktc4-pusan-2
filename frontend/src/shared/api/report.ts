import { type QueryClient, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from './client';
import { ApiFailure } from './tokenStore';
import type { SlideCoverageDetail, TakeReport } from '@/types/report';

/** 분석이 끝나기 전에는 이 간격으로 다시 묻습니다 */
const POLL_MS = 2000;

/**
 * BE 가 `content_status` 를 내기 전(BE 미구현 — #1153) 응답에는 키가 없습니다.
 * 키는 늘 두고 값은 null 로 맞춥니다 (AGENTS.md 6번)
 */
export async function fetchTakeReport(takeId: string): Promise<TakeReport> {
  const report = await apiRequest<
    Omit<TakeReport, 'content_status'> & Partial<Pick<TakeReport, 'content_status'>>
  >(`/api/takes/${takeId}/report`);
  return { ...report, content_status: report.content_status ?? null };
}

/**
 * 더 기다려도 바뀌지 않는 상태인가. 분석에 실패했거나, 리뷰가 끝났거나(실패 · 건너뜀 포함), 단계가 DONE 이거나,
 * 리뷰가 보류됐으면 그렇습니다. 리뷰가 실패해도 측정값은 남아 있어 리포트로 넘어갑니다.
 */
export function isSettled(report: TakeReport | undefined): boolean {
  if (!report) return false;
  return (
    report.status === 'FAILED' ||
    report.review_status !== null ||
    report.analysis_stage === 'DONE' ||
    report.analysis_hold_reason !== null
  );
}

export const takeReportKey = (takeId: string) => ['takes', takeId, 'report'] as const;

export function useTakeReport(takeId: string) {
  return useQuery({
    queryKey: takeReportKey(takeId),
    queryFn: () => fetchTakeReport(takeId),
    refetchInterval: (query) => (isSettled(query.state.data) ? false : POLL_MS),
  });
}

/** 확인 답으로 대본 전달도 결과가 바뀌었다 — 리포트를 다시 읽고 펼친 장도 새 판으로 다시 부릅니다 */
export const COVERAGE_REVISION_CHANGED = 'COVERAGE_REVISION_CHANGED';

/**
 * 장별 상세 (BE 미구현 — #1153 6절). 예전 결과는 `failure.fallback` 키가 없어 null 로 맞춥니다
 */
export async function fetchSlideCoverage(
  takeId: string,
  slideNumber: number,
  revision: number,
): Promise<SlideCoverageDetail> {
  const detail = await apiRequest<SlideCoverageDetail>(
    `/api/takes/${takeId}/script-coverage/slides/${slideNumber}?revision=${revision}`,
  );
  if (!detail.failure) return detail;
  return { ...detail, failure: { ...detail.failure, fallback: detail.failure.fallback ?? null } };
}

export const slideCoverageKey = (takeId: string, slideNumber: number, revision: number) =>
  ['takes', takeId, 'script-coverage', slideNumber, revision] as const;

/** 404 · 409 는 다시 물어도 같습니다 */
function retryable(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiFailure && (error.status === 404 || error.status === 409)) return false;
  return failureCount < 1;
}

/**
 * 장별 상세 쿼리. 409 `COVERAGE_REVISION_CHANGED` 면 리포트를 무효화합니다 —
 * 리포트가 새 `revision` 으로 오면 쿼리 키가 바뀌어 펼친 장을 새 판으로 다시 부릅니다
 */
export function slideCoverageQuery(
  client: QueryClient,
  takeId: string,
  slideNumber: number,
  revision: number,
) {
  return {
    queryKey: slideCoverageKey(takeId, slideNumber, revision),
    queryFn: async () => {
      try {
        return await fetchSlideCoverage(takeId, slideNumber, revision);
      } catch (error) {
        if (error instanceof ApiFailure && error.code === COVERAGE_REVISION_CHANGED)
          client.invalidateQueries({ queryKey: takeReportKey(takeId) }).catch(() => undefined);
        throw error;
      }
    },
    retry: retryable,
  };
}

/** 장을 펼칠 때만 부릅니다(`enabled`) */
export function useSlideCoverage(
  takeId: string,
  slideNumber: number,
  revision: number,
  enabled: boolean,
) {
  const client = useQueryClient();
  return useQuery({ ...slideCoverageQuery(client, takeId, slideNumber, revision), enabled });
}
