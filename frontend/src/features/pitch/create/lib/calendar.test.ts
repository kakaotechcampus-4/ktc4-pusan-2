import { describe, expect, it } from 'vitest';
import { buildMonth, dotted, shiftMonth } from './calendar';

describe('달력 — 시안 2026년 10월', () => {
  const cells = buildMonth(2026, 10);

  it('6주 42칸이다', () => {
    expect(cells).toHaveLength(42);
  });

  /** 시안: 첫 줄이 27 28 29 30 1 2 3 */
  it('10월 1일은 목요일 칸이고 앞은 9월 말이다', () => {
    expect(cells.slice(0, 7).map((c) => c.day)).toEqual([27, 28, 29, 30, 1, 2, 3]);
    expect(cells[4]).toMatchObject({ iso: '2026-10-01', inMonth: true, weekday: 4 });
    expect(cells[0]).toMatchObject({ iso: '2026-09-27', inMonth: false });
  });

  it('9일은 금요일이다 — 시안의 선택된 날', () => {
    expect(cells.find((c) => c.iso === '2026-10-09')?.weekday).toBe(5);
  });
});

describe('달 이동', () => {
  it('해를 넘긴다', () => {
    expect(shiftMonth(2026, 12, 1)).toEqual({ year: 2027, month: 1 });
    expect(shiftMonth(2026, 1, -1)).toEqual({ year: 2025, month: 12 });
  });

  it('점으로 표기한다', () => {
    expect(dotted('2026-10-09')).toBe('2026.10.09');
  });
});
