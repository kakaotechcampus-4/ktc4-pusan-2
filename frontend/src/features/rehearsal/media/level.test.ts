import { describe, expect, it } from 'vitest';
import { isSilentFrame } from './level';

describe('조용한 블록인가 (무음 경고 · 마이크 입력 확인)', () => {
  it('작은 소리라도 말소리로 들리면 조용하지 않다 — 평소 목소리 읽기의 "들려요"와 엇갈리지 않게', () => {
    // 자동 음량 조절을 끈 원본에서 작게 말하면 0.005(약 -46dBFS) 아래로 내려갑니다
    expect(isSilentFrame(0.002, true)).toBe(false);
  });

  it('말소리도 아니고 작으면 조용하다 — 입력이 없는 마이크는 그대로 잡는다', () => {
    expect(isSilentFrame(0, false)).toBe(true);
    expect(isSilentFrame(0.002, false)).toBe(true);
  });

  it('크면 말소리가 아니어도 조용하지 않다', () => {
    expect(isSilentFrame(0.05, false)).toBe(false);
  });
});
