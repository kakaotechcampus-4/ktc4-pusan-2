import { QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

// 시험 환경에는 앱 주소가 없어 상대 경로를 부를 수 없습니다 — `apiBase` 가 읽기 전에 정합니다
vi.hoisted(() => {
  import.meta.env.VITE_API_BASE = 'http://localhost';
});

import { reportHandlers } from '@/mocks/report';
import { fetchSlideCoverage, fetchTakeReport, slideCoverageQuery, takeReportKey } from './report';
import { ApiFailure } from './tokenStore';

/** 대본 전달도 상세(#1154)의 요청 함수를 목 BE(MSW) 위에서 그대로 부릅니다 (BE 미구현 — #1153) */
const server = setupServer(...reportHandlers);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const failureOf = async (promise: Promise<unknown>) => {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiFailure) return { status: error.status, code: error.code };
    throw error;
  }
  throw new Error('실패하지 않았습니다');
};

describe('fetchTakeReport — content_status', () => {
  it('BE 배포 전 응답처럼 키가 없으면 null 로 맞춥니다', async () => {
    server.use(
      http.get('*/api/takes/:takeId/report', () =>
        HttpResponse.json({ take_id: 'old', status: 'COMPLETED', review: null }),
      ),
    );
    const report = await fetchTakeReport('old');
    expect(report).toHaveProperty('content_status', null);
  });

  it('목의 Take id 마다 상태가 다릅니다', async () => {
    expect((await fetchTakeReport('t3')).content_status?.state).toBe('MEASURED');
    expect((await fetchTakeReport('t-content-none')).content_status).toBeNull();
    expect((await fetchTakeReport('t-content-analyzing')).content_status?.state).toBe('ANALYZING');
    const legacy = (await fetchTakeReport('t-content-legacy')).content_status;
    expect(legacy).toMatchObject({
      state: 'NOT_MEASURED',
      reason_code: 'LEGACY_RESULT',
      summary: null,
    });
    expect((await fetchTakeReport('t-failed')).content_status?.reason_code).toBe('TAKE_FAILED');
  });

  it('MEASURED 요약은 #1152 예시 숫자 그대로입니다', async () => {
    const status = (await fetchTakeReport('t3')).content_status!;
    expect(status.revision).toBe(0);
    expect(status.summary?.delivery).toMatchObject({ value: 0.5, state: 'provisional' });
    expect(status.summary?.scope).toMatchObject({ scored_slides: 1, estimated_slides: 1 });
    expect(status.summary?.slides.map((s) => s.state)).toEqual(['scored', 'estimated']);
  });
});

describe('fetchSlideCoverage', () => {
  it('채점된 장 · 간이 장 · 실패 장을 돌려줍니다', async () => {
    const scored = await fetchSlideCoverage('t3', 3, 0);
    expect(scored).toMatchObject({ take_id: 't3', revision: 0, state: 'SCORED', failure: null });
    expect(scored.stt_sentences.length).toBeGreaterThan(0);

    const estimated = await fetchSlideCoverage('t3', 4, 0);
    expect(estimated.state).toBe('ESTIMATED');
    expect(estimated.failure?.fallback?.delivery_band).toBe('about_half');
  });

  it('예전 결과처럼 fallback 키가 없는 실패 장은 null 로 맞춥니다', async () => {
    const failed = await fetchSlideCoverage('t-content-zero', 6, 0);
    expect(failed.state).toBe('FAILED');
    expect(failed.failure).toHaveProperty('fallback', null);
    expect(failed.detail).toBeNull();
    expect(failed.sentences).toEqual([]);
  });

  it('요약에 없는 장은 404 SLIDE_NOT_FOUND, 확정본이 없으면 404 COVERAGE_NOT_FOUND', async () => {
    expect(await failureOf(fetchSlideCoverage('t3', 9, 0))).toEqual({
      status: 404,
      code: 'SLIDE_NOT_FOUND',
    });
    expect(await failureOf(fetchSlideCoverage('t-content-none', 3, 0))).toEqual({
      status: 404,
      code: 'COVERAGE_NOT_FOUND',
    });
  });
});

describe('slideCoverageQuery — 409 COVERAGE_REVISION_CHANGED', () => {
  it('리포트를 무효화하고, 새 판으로 다시 부르면 받습니다', async () => {
    const client = new QueryClient();
    const takeId = 't-content-revised';
    const report = await client.fetchQuery({
      queryKey: takeReportKey(takeId),
      queryFn: () => fetchTakeReport(takeId),
    });
    expect(report.content_status?.revision).toBe(0);

    const stale = client.fetchQuery(slideCoverageQuery(client, takeId, 3, 0));
    expect(await failureOf(stale)).toEqual({ status: 409, code: 'COVERAGE_REVISION_CHANGED' });
    expect(client.getQueryState(takeReportKey(takeId))?.isInvalidated).toBe(true);

    const fresh = await fetchTakeReport(takeId);
    expect(fresh.content_status?.revision).toBe(1);
    const detail = await client.fetchQuery(slideCoverageQuery(client, takeId, 3, 1));
    expect(detail).toMatchObject({ revision: 1, state: 'SCORED' });
  });

  it('404 · 409 는 다시 묻지 않습니다', () => {
    const { retry } = slideCoverageQuery(new QueryClient(), 't3', 3, 0);
    expect(retry(0, new ApiFailure(404, 'SLIDE_NOT_FOUND'))).toBe(false);
    expect(retry(0, new ApiFailure(409, 'COVERAGE_REVISION_CHANGED'))).toBe(false);
    expect(retry(0, new ApiFailure(500, 'REQUEST_FAILED'))).toBe(true);
    expect(retry(1, new ApiFailure(500, 'REQUEST_FAILED'))).toBe(false);
  });
});
