import { describe, expect, it } from 'vitest';
import { indicatorViews } from './coachIndicators';

describe('상태 표시', () => {
  it('화면 순서대로 이름과 뜻을 붙인다', () => {
    expect(indicatorViews({ TIME: 'AHEAD', GAZE: 'SCRIPT', SPEED: 'NORMAL' })).toEqual([
      { area: 'GAZE', label: '시선', value: '대본', tone: 'warn' },
      { area: 'SPEED', label: '속도', value: '좋음', tone: 'good' },
      { area: 'TIME', label: '시간', value: '빠름', tone: 'warn' },
    ]);
  });

  it('아직 받지 못한 영역은 그리지 않는다', () => {
    expect(indicatorViews({})).toEqual([]);
  });

  it('잴 수 없거나 모르는 상태는 좋다 나쁘다 하지 않는다', () => {
    expect(indicatorViews({ GAZE: 'UNMEASURABLE', FILLER: 'SOMETHING_NEW' })).toEqual([
      { area: 'GAZE', label: '시선', value: '—', tone: 'unknown' },
      { area: 'FILLER', label: '군더더기', value: '—', tone: 'unknown' },
    ]);
  });
});
