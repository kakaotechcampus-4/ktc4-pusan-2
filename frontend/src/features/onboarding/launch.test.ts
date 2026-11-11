import { describe, expect, it } from 'vitest';
import { countdownParts, LAUNCH_AT } from './launch';

describe('출시 카운트다운', () => {
  it('다른 타임존 표기도 동일한 공개 순간이다', () => {
    expect(Date.parse(LAUNCH_AT)).toBe(Date.parse('2026-10-30T05:00:00Z'));
  });

  it('직전·정각·직후와 하루 경계에서 음수 없이 계산한다', () => {
    expect(countdownParts(1)).toEqual([0, 0, 0, 1]);
    expect(countdownParts(0)).toEqual([0, 0, 0, 0]);
    expect(countdownParts(-1000)).toEqual([0, 0, 0, 0]);
    expect(countdownParts(90061000)).toEqual([1, 1, 1, 1]);
  });
});
