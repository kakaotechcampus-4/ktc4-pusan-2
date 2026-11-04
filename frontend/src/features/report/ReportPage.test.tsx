// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReportPage } from './ReportPage';
import { measuresFixture, reviewFixture } from '@/mocks/report';
import type { TakeReport } from '@/types/report';

const query = vi.hoisted(() => ({
  data: undefined as TakeReport | undefined,
  error: null as Error | null,
  isPending: false,
}));
vi.mock('@/shared/api/report', () => ({ useTakeReport: () => query }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  query.error = null;
  query.data = {
    take_id: 't3',
    pitch_id: 'p1',
    pitch_title: '연습',
    take_number: 3,
    script_mode: 'HIGHLIGHT',
    ended_at: '2026-10-26T00:00:00Z',
    status: 'COMPLETED',
    analysis_stage: 'DONE',
    review_status: 'DONE',
    analysis_hold_reason: null,
    measures: measuresFixture,
    review: reviewFixture,
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function render() {
  await act(async () =>
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/takes/t3']}>
          <Routes>
            <Route path="/takes/:takeId" element={<ReportPage />} />
            <Route path="/" element={<p>홈 도착</p>} />
            <Route path="/pitch/p1/device-check" element={<p>장치 점검 도착</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}
it('shows the previous Take comparison immediately after the summary and before measures', async () => {
  await render();
  const main = host.querySelector('main')!;
  const summary = main.querySelector('[role="status"]')!.closest('section')!;
  const comparison = summary.nextElementSibling!;
  expect(comparison.querySelector('h2')?.textContent).toBe('지난 Take와 비교');
  expect(comparison.nextElementSibling?.getAttribute('aria-label')).toBe('측정값');
  expect(main.querySelectorAll('h2')).toHaveLength(8);
});
