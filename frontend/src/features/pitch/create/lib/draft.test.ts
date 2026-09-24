import { describe, expect, it } from 'vitest';
import { countChars, estimateDurationMs, formatEstimate } from './draft';

describe('대본 분량과 예상 시간', () => {
  it('공백은 세지 않는다', () => {
    expect(countChars('가 나\n다\t라')).toBe(4);
  });

  /**
   * 목업 06 의 "1,284자 · 예상 04:52" 를 그대로 맞춥니다.
   * 이 숫자가 어긋나면 상수(EST_CHARS_PER_MIN)가 바뀐 것이고,
   * 그때는 준비 화면의 estBasisWpm 과도 어긋납니다.
   */
  it('1,284자는 04:52 로 보인다 — 목업 06', () => {
    expect(formatEstimate(estimateDurationMs('가'.repeat(1_284)))).toBe('04:52');
  });

  it('빈 대본은 00:00', () => {
    expect(formatEstimate(estimateDurationMs(''))).toBe('00:00');
  });

  it('한 자리 초도 두 자리로 적는다', () => {
    expect(formatEstimate(5_000)).toBe('00:05');
  });
});
