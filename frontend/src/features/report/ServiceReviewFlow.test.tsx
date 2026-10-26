// @vitest-environment jsdom
import { act, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, Link, useNavigate } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReportPage } from './ReportPage';
import { measuresFixture, reviewFixture } from '@/mocks/report';
import type { TakeReport } from '@/types/report';
import { markReviewPreviewReady } from '@/shared/lib/serviceReviewPreview';

const query = vi.hoisted(() => ({
  data: undefined as TakeReport | undefined,
  error: null as Error | null,
  isPending: false,
}));
vi.mock('@/shared/api/report', () => ({ useTakeReport: () => query }));
vi.mock('@/shared/api/serviceReview', () => ({
  serviceReviewPreview: true,
  submitServiceReview: vi.fn(),
}));
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
      </StrictMode>,
    ),
  );
}
it.each(['내 피치로', 'Take 4 연습하기'])(
  'opens only on %s and preserves its destination',
  async (label) => {
    markReviewPreviewReady('t3');
    await render();
    expect(host.querySelector('dialog')).toBeNull();
    await act(async () =>
      [...host.querySelectorAll('a')].find((a) => a.textContent === label)!.click(),
    );
    expect(host.querySelector('dialog')?.open).toBe(true);
    await act(async () =>
      [...host.querySelectorAll('button')].find((b) => b.textContent === '나중에')!.click(),
    );
    expect(host.textContent).toContain(label === '내 피치로' ? '홈 도착' : '장치 점검 도착');
  },
);
it.each(['old', 'loading', 'failed-review', 'query-error'])(
  'does not prompt for %s',
  async (scenario) => {
    if (scenario !== 'old') markReviewPreviewReady('t3');
    if (scenario === 'loading') query.data!.analysis_stage = 'REVIEWING';
    if (scenario === 'failed-review') query.data!.review_status = 'FAILED';
    if (scenario === 'query-error') query.error = new Error('offline');
    await render();
    await act(async () =>
      [...host.querySelectorAll('a')].find((a) => a.textContent === '내 피치로')!.click(),
    );
    expect(host.querySelector('dialog')).toBeNull();
    expect(host.textContent).toContain('홈 도착');
  },
);

it.each(['header', 'history'])(
  'expires an unused opportunity after %s exit and return',
  async (exit) => {
    markReviewPreviewReady('t3');
    await render();
    await act(async () => {
      if (exit === 'header')
        host.querySelector<HTMLAnchorElement>('a[aria-label="홈으로"]')!.click();
      else [...host.querySelectorAll('button')].find((b) => b.textContent === '뒤로')!.click();
    });
    expect(host.textContent).toContain('홈 도착');
    await act(async () => {
      if (exit === 'header')
        [...host.querySelectorAll('a')].find((a) => a.textContent === '기록 다시 열기')!.click();
      else [...host.querySelectorAll('button')].find((b) => b.textContent === '앞으로')!.click();
    });
    await act(async () =>
      [...host.querySelectorAll('a')].find((a) => a.textContent === '내 피치로')!.click(),
    );
    expect(host.querySelector('dialog')).toBeNull();
    expect(host.textContent).toContain('홈 도착');
  },
);
