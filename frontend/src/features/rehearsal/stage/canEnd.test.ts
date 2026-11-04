import { describe, expect, it } from 'vitest';
import { canEnd } from './canEnd';

describe('끝내기를 열어도 되나', () => {
  it('마지막 장이 아니면 막는다', () => {
    expect(canEnd(5, 1)).toBe(false);
    expect(canEnd(5, 4)).toBe(false);
  });

  it('마지막 장에서 연다', () => {
    expect(canEnd(5, 5)).toBe(true);
    expect(canEnd(1, 1)).toBe(true);
  });

  it('장 수를 모르면(0) 막지 않는다', () => {
    expect(canEnd(0, 1)).toBe(true);
  });
});
