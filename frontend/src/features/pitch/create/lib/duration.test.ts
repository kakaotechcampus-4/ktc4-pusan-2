import { describe, expect, it } from 'vitest';
import { allowedRange, daysUntil, formatSeconds } from './duration';

describe('발표 시간 표기 — 시안 발표정보', () => {
  it('0 인 단위는 적지 않는다', () => {
    expect(formatSeconds(300)).toBe('5분');
    expect(formatSeconds(30)).toBe('30초');
    expect(formatSeconds(270)).toBe('4분 30초');
  });

  it('5분 −30초 +1분 은 4분 30초 ~ 6분', () => {
    const { minSec, maxSec } = allowedRange(300, 30, 60);
    expect(`${formatSeconds(minSec)} ~ ${formatSeconds(maxSec)}`).toBe('4분 30초 ~ 6분');
  });

  it('하한이 목표보다 커도 0 아래로 내려가지 않는다', () => {
    expect(allowedRange(60, 120, 0).minSec).toBe(0);
  });
});

describe('D-day', () => {
  const now = new Date('2026-10-03T15:00:00');

  it('시안: 10월 3일 기준 10월 9일은 D-6', () => {
    expect(daysUntil('2026-10-09', now)).toBe(6);
  });

  it('당일은 D-0, 지난 날짜와 빈 값은 null', () => {
    expect(daysUntil('2026-10-03', now)).toBe(0);
    expect(daysUntil('2026-10-02', now)).toBeNull();
    expect(daysUntil('', now)).toBeNull();
  });
});
