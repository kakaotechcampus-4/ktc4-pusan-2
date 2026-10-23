import { afterEach, expect, it, vi } from 'vitest';
import { fetchTakeReport, isSettled } from '@/shared/api/report';
import { clearTokens } from '@/shared/api/tokenStore';
import type { TakeReport } from '@/types/report';
import { fmt, pct, stepStates } from './reportFormat';

afterEach(() => {
  vi.unstubAllGlobals();
  clearTokens();
});

const analyzing: TakeReport = {
  take_id: 't4',
  pitch_id: 'p1',
  pitch_title: '캡스톤 최종 발표',
  take_number: 4,
  script_mode: 'HIGHLIGHT',
  ended_at: null,
  status: 'ANALYZING',
  analysis_stage: 'SCORING',
  review_status: null,
  measures: null,
  review: null,
};

it('spreads each BE stage over the three on-screen steps', () => {
  expect(stepStates(null)).toEqual(['RUNNING', 'PENDING', 'PENDING']);
  expect(stepStates('FINALIZING')).toEqual(['RUNNING', 'PENDING', 'PENDING']);
  expect(stepStates('SCORING')).toEqual(['DONE', 'RUNNING', 'PENDING']);
  expect(stepStates('AWAITING_CONFIRMATION')).toEqual(['DONE', 'WAITING', 'PENDING']);
  expect(stepStates('REVIEWING')).toEqual(['DONE', 'DONE', 'RUNNING']);
  expect(stepStates('DONE')).toEqual(['DONE', 'DONE', 'DONE']);
});

it('keeps polling until the take fails, the review ends, or the stage is DONE', () => {
  expect(isSettled(undefined)).toBe(false);
  expect(isSettled(analyzing)).toBe(false);
  expect(isSettled({ ...analyzing, analysis_stage: 'AWAITING_CONFIRMATION' })).toBe(false);
  expect(isSettled({ ...analyzing, status: 'FAILED' })).toBe(true);
  // 리뷰가 실패해도 측정값은 있어 리포트로 넘어갑니다
  expect(isSettled({ ...analyzing, review_status: 'FAILED' })).toBe(true);
  expect(isSettled({ ...analyzing, analysis_stage: 'DONE' })).toBe(true);
});

it('shows missing measures as — instead of 0%', () => {
  expect(pct(null)).toBe('—');
  expect(pct(0)).toBe('0%');
  expect(fmt('script_ratio', 0.62)).toBe('62%');
  expect(fmt('unknown_metric', 3)).toBe('3');
});

it('asks the report endpoint with the take id and keeps errors as failures', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(analyzing));
  vi.stubGlobal('fetch', fetch);
  expect(await fetchTakeReport('t4')).toEqual(analyzing);
  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/takes\/t4\/report$/);

  fetch.mockResolvedValue(Response.json({ code: 'TAKE_NOT_FOUND' }, { status: 404 }));
  await expect(fetchTakeReport('t4')).rejects.toMatchObject({ status: 404 });
});
