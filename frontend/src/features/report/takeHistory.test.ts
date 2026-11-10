import { describe, expect, it } from 'vitest';
import type { TakeHistoryRow } from '@/types/takeHistory';
import {
  completedTakes,
  gazeOffEngine,
  latestGazeEngine,
  scriptModeLabel,
  takeDay,
} from './takeHistoryFormat';

const row = (patch: Partial<TakeHistoryRow>): TakeHistoryRow => ({
  take_id: 't1',
  pitch_id: 'p1',
  pitch_title: '캡스톤',
  is_demo: false,
  take_number: 1,
  started_at: '2026-10-06T12:00:00',
  status: 'COMPLETED',
  end_reason: null,
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
  ...patch,
});

describe('Take 목록 표시', () => {
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

it('최신 Take에 엔진이 없으면 가장 최근의 측정된 엔진으로 비교한다', () => {
  expect(latestGazeEngine([row({ gaze_engine: null }), row({}), row({ gaze_engine: 'old' })])).toBe(
    'gaze_v1.1.0',
  );
  expect(latestGazeEngine([row({ gaze_engine: null })])).toBeNull();
});

describe('대본 보기 값을 한글로 (리포트 비교)', () => {
  it('세 방식은 한글 이름이다', () => {
    expect(scriptModeLabel('HIGHLIGHT')).toBe('전체 대본 + 하이라이트');
    expect(scriptModeLabel('KEYWORD')).toBe('핵심 키워드');
    expect(scriptModeLabel('OFF')).toBe('대본 없이');
  });

  it('예전 FULL 은 전체 대본, 모르는 값은 영어 코드 대신 "다른 방식"이다', () => {
    expect(scriptModeLabel('FULL')).toBe('전체 대본');
    expect(scriptModeLabel('SOMETHING_NEW')).toBe('다른 방식');
  });

  it('toString 같은 상속 키도 모르는 값으로 본다', () => {
    expect(scriptModeLabel('toString')).toBe('다른 방식');
    expect(scriptModeLabel('constructor')).toBe('다른 방식');
  });
});
