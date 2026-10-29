// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProcessingPage } from './ProcessingPage';

const report = vi.hoisted(() => ({ analysis_stage: 'SCORING' }));
vi.mock('./api/report', () => ({
  useTakeReport: () => ({ data: report, isPending: false, error: null }),
  isSettled: () => false,
}));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.useFakeTimers();
  report.analysis_stage = 'SCORING';
  vi.spyOn(Math, 'random').mockReturnValue(0);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it('pauses and resumes automatic tips while keeping the live analysis status available', async () => {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/takes/t1/processing']}>
        <Routes>
          <Route path="/takes/:takeId/processing" element={<ProcessingPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
  const tip = () => host.querySelector('button')!.previousElementSibling!.textContent;
  const first = tip();
  await act(async () => vi.advanceTimersByTime(6000));
  expect(tip()).not.toBe(first);
  await act(async () => host.querySelector('button')!.click());
  const paused = tip();
  expect(host.querySelector('button')?.getAttribute('aria-pressed')).toBe('true');
  await act(async () => vi.advanceTimersByTime(18000));
  expect(tip()).toBe(paused);
  await act(async () => host.querySelector('button')!.click());
  await act(async () => vi.advanceTimersByTime(6000));
  expect(tip()).not.toBe(paused);
  expect(host.querySelector('[role="status"]')?.textContent).toContain('분석 중');
});
it('keeps the confirmation link and a single shared screen while tips can be paused', async () => {
  report.analysis_stage = 'AWAITING_CONFIRMATION';
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/takes/t1/processing']}>
        <Routes>
          <Route path="/takes/:takeId/processing" element={<ProcessingPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
  expect(host.querySelector('a[href="/takes/t1/confirm"]')?.textContent).toContain('확인하기');
  expect(host.querySelectorAll('header')).toHaveLength(1);
  expect(host.querySelectorAll('main')).toHaveLength(1);
  expect(host.querySelector('h1')?.textContent).toBe('분석 대기');
  expect(host.querySelector('button[aria-pressed="false"]')).not.toBeNull();
});
