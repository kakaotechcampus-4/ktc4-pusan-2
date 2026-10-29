import { expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter, Route, Routes } from 'react-router';
import { ReportPage } from './ReportPage';

vi.mock('./api/report', () => ({
  useTakeReport: () => ({
    isPending: false,
    error: null,
    data: {
      take_id: 't1',
      pitch_id: 'p1',
      pitch_title: '발표',
      take_number: 1,
      script_mode: 'HIGHLIGHT',
      ended_at: null,
      status: 'COMPLETED',
      analysis_hold_reason: null,
      measures: null,
      review: {
        overall_summary: '총평',
        score: null,
        dimensions: { script_independence: 'UNKNOWN', pacing_rhythm: 'STABLE' },
        strengths: [{ area: 'NEW_AREA', slide_number: null, text: '강점', evidence: {} }],
        improvements: [],
        comparison: [],
        mission_results: [],
        criteria_results: [],
        next_missions: [
          {
            area: 'TIME',
            slide_number: null,
            description: '다음 연습',
            reason: null,
            priority: 1,
            target: { metric: 'duration_ms', operator: 'LTE', value: 1000 },
          },
        ],
        fallback_reason: null,
      },
    },
  }),
}));

it('알 수 없는 영역·상태와 null 미션 사유가 와도 리포트를 표시한다', () => {
  const html = renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/takes/t1']}>
        <Routes>
          <Route path="/takes/:takeId" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(html).toContain('판단 불가');
  expect(html).toContain('NEW_AREA · 발표 전체');
  expect(html).toContain('다음 연습');
  expect(html).not.toContain('undefined');
  expect(html).not.toContain('· null');
});
