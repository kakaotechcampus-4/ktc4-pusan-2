// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, Link, useNavigate } from 'react-router';
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
  sessionStorage.clear();
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
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
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
function HistoryButtons() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(-1)}>뒤로</button>
      <button onClick={() => navigate(1)}>앞으로</button>
    </>
  );
}
async function render() {
  await act(async () =>
    root.render(
      <StrictMode>
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={['/', '/takes/t3']} initialIndex={1}>
            <HistoryButtons />
            <Routes>
              <Route path="/takes/:takeId" element={<ReportPage />} />
              <Route
                path="/"
                element={
                  <>
                    <p>홈 도착</p>
                    <Link to="/takes/t3">기록 다시 열기</Link>
                  </>
                }
              />
              <Route path="/pitch/p1/device-check" element={<p>장치 점검 도착</p>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </StrictMode>,
    ),
  );
}
it.each(['내 피치로', 'Take 4 연습하기', 'PITCH COACH 홈'])(
  'navigates immediately without a review dialog: %s',
  async (label) => {
    await render();
    expect(host.querySelectorAll('a[aria-label="PITCH COACH 홈"]')).toHaveLength(1);
    await act(async () =>
      [...host.querySelectorAll('a')]
        .find((a) => a.textContent === label || a.getAttribute('aria-label') === label)!
        .click(),
    );
    expect(host.querySelector('dialog')).toBeNull();
    expect(host.textContent).toContain(label === 'Take 4 연습하기' ? '장치 점검 도착' : '홈 도착');
  },
);
