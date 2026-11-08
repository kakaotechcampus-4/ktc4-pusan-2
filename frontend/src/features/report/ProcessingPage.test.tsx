// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProcessingPage } from './ProcessingPage';

const report = vi.hoisted(() => ({
  analysis_stage: 'SCORING',
  status: 'ANALYZING',
  analysis_hold_reason: null as string | null,
}));
const settled = vi.hoisted(() => ({ value: false }));
vi.mock('@/shared/api/report', () => ({
  useTakeReport: () => ({ data: report, isPending: false, error: null }),
  isSettled: () => settled.value,
}));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.useFakeTimers();
  report.analysis_stage = 'SCORING';
  report.status = 'ANALYZING';
  report.analysis_hold_reason = null;
  settled.value = false;
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

/** 리포트 자리에 들어온 라우터 state 를 그대로 적어 둡니다 */
function ReportStub() {
  return <pre data-testid="report-state">{JSON.stringify(useLocation().state)}</pre>;
}

function renderAt(state: unknown) {
  return act(async () =>
    root.render(
      <MemoryRouter initialEntries={[{ pathname: '/takes/t1/processing', state }]}>
        <Routes>
          <Route path="/takes/:takeId/processing" element={<ProcessingPage />} />
          <Route path="/takes/:takeId" element={<ReportStub />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

// #925 서버가 말 없음으로 끝낸 Take 는 목표 시간보다 일찍 끝나 보여, 이유를 화면 읽기 프로그램에도 알립니다
it('announces a silence auto end with the server message and why it differs from the goal time', async () => {
  await renderAt({
    autoEnd: { reason: 'SILENCE', message: '2분 넘게 말이 없어 연습을 자동으로 끝냈어요.' },
  });
  const alert = host.querySelector('[role="alert"]');
  expect(alert?.textContent).toContain('자동 종료');
  expect(alert?.textContent).toContain('2분 넘게 말이 없어 연습을 자동으로 끝냈어요.');
  expect(alert?.textContent).toContain('발표 목표 시간과는 상관없이');
  // 분석은 그대로 진행 중으로 보입니다
  expect(host.querySelector('[role="status"]')?.textContent).toContain('분석 중');
});

// 기록 접수 표시는 분석이 이어지는 동안만 — 실패 · 보류 안내 위에 띄우면 끝난 분석처럼 읽힙니다
it('shows the recorded take cue only while the analysis is still going', async () => {
  await renderAt(null);
  expect(host.textContent).toContain('TAKE RECORDED');

  report.status = 'FAILED';
  await renderAt(null);
  expect(host.textContent).not.toContain('TAKE RECORDED');

  report.status = 'ANALYZING';
  report.analysis_hold_reason = 'REVIEW_FAILED';
  await renderAt(null);
  expect(host.textContent).not.toContain('TAKE RECORDED');
});

it('shows the time limit reason through the same notice', async () => {
  await renderAt({ autoEnd: { reason: 'TIME_LIMIT', message: '' } });
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(
    '연습은 최대 1시간까지라 자동으로 끝냈어요.',
  );
});

it('shows no auto end notice when the user ended the take', async () => {
  await renderAt(null);
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

it('carries the auto end notice to the report so a fast analysis does not hide it', async () => {
  settled.value = true;
  await renderAt({ autoEnd: { reason: 'SILENCE', message: '2분 넘게 말이 없어요.' } });
  expect(JSON.parse(host.querySelector('[data-testid="report-state"]')!.textContent!)).toEqual({
    autoEnd: { reason: 'SILENCE', message: '2분 넘게 말이 없어요.' },
  });
});

it('moves to the report without state when the take was not auto ended', async () => {
  settled.value = true;
  await renderAt({ clientSessionId: 's1' });
  expect(host.querySelector('[data-testid="report-state"]')?.textContent).toBe('null');
});
