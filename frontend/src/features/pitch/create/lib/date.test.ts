import { describe, expect, it } from 'vitest';
import { daysUntil, monthGrid, toIsoDate } from './date';

describe('발표 날짜', () => {
  it('로컬 날짜로 적는다 — UTC 로 하루 밀리지 않는다', () => {
    expect(toIsoDate(new Date(2026, 9, 9, 0, 30))).toBe('2026-10-09');
  });

  it('목업의 D-6 — 10월 3일에 10월 9일은 6일 남았다', () => {
    expect(daysUntil('2026-10-09', new Date(2026, 9, 3, 15))).toBe(6);
  });

  it('지난 날짜와 빈 값은 배지를 그리지 않는다', () => {
    expect(daysUntil('2026-10-02', new Date(2026, 9, 3))).toBeNull();
    expect(daysUntil('', new Date(2026, 9, 3))).toBeNull();
  });

  /** 목업 달력: 2026년 10월은 9월 27일(일)에서 시작해 10월 31일(토)에서 끝납니다 */
  it('2026년 10월 달력은 5주이고 앞쪽을 9월로 채운다', () => {
    const grid = monthGrid(2026, 9);
    expect(grid).toHaveLength(35);
    expect(grid[0]).toMatchObject({ iso: '2026-09-27', outside: true, weekday: 0 });
    expect(grid[4]).toMatchObject({ iso: '2026-10-01', outside: false });
    expect(grid.at(-1)).toMatchObject({ iso: '2026-10-31', weekday: 6, outside: false });
  });
});
