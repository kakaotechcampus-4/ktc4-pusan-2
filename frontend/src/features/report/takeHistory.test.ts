import { describe, expect, it } from 'vitest';
import type { TakeHistoryRow } from '@/types/takeHistory';
import { completedTakes, gazeOffEngine, pct, takeDay } from './takeHistoryFormat';

const row = (patch: Partial<TakeHistoryRow>): TakeHistoryRow => ({
  take_id: 't1',
  pitch_id: 'p1',
  pitch_title: '캡스톤',
  take_number: 1,
  started_at: '2026-10-06T12:00:00',
  status: 'COMPLETED',
  script_mode: 'HIGHLIGHT',
  duration_ms: 1000,
  goal_ms: 1000,
  audience_ratio: 0.5,
  script_ratio: 0.5,
  cpm: 300,
  filler_count: 1,
  missions_achieved: 0,
  missions_total: 0,
  gaze_engine: 'gaze_v1.1.0',
  is_best: false,
  ...patch,
});

describe('Take 목록 표시', () => {
  it('측정 못 한 비율은 0% 가 아니라 — 입니다', () => {
    expect(pct(null)).toBe('—');
    expect(pct(0)).toBe('0%');
    expect(pct(0.736)).toBe('74%');
  });

  it('날짜는 월.일 두 자리씩', () => {
    expect(takeDay('2026-03-05T12:00:00')).toBe('03.05');
  });

  it('비교에는 분석이 끝난 Take 만 씁니다', () => {
    const rows = [row({ status: 'ANALYZING' }), row({ take_id: 't2' }), row({ status: 'FAILED' })];
    expect(completedTakes(rows).map((r) => r.take_id)).toEqual(['t2']);
  });

  it('최신 Take 와 시선 엔진이 다르거나 시선을 못 쟀으면 시선 값을 비교하지 않습니다', () => {
    expect(gazeOffEngine(row({}), 'gaze_v1.1.0')).toBe(false);
    expect(gazeOffEngine(row({ gaze_engine: 'gaze_v1.0.0' }), 'gaze_v1.1.0')).toBe(true);
    expect(gazeOffEngine(row({ gaze_engine: null }), 'gaze_v1.1.0')).toBe(true);
    expect(gazeOffEngine(row({}), null)).toBe(true);
  });
});
