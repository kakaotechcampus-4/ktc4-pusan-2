import { describe, expect, it } from 'vitest';
import { createSlideTimeline } from './slideTimeline';

describe('그 시각에 보이던 슬라이드', () => {
  it('넘기기 전에는 처음 슬라이드다', () => {
    const timeline = createSlideTimeline(1);
    expect(timeline.at(0)).toBe(1);
    expect(timeline.at(99_000)).toBe(1);
  });

  it('늦게 나가는 1초도 그 1초가 시작한 시각의 번호를 받는다 — 30초에 2 → 3', () => {
    // 시선이 없어 스스로 잇는 1초는 2~3초 늦게 나갑니다. 보내는 순간의 번호면 셋 다 3번이었습니다
    const timeline = createSlideTimeline(2);
    timeline.change(30_000, 3);
    expect([27_000, 28_000, 29_000].map((ms) => timeline.at(ms))).toEqual([2, 2, 2]);
    expect(timeline.at(30_000)).toBe(3);
  });

  it('연달아 넘겨도 시각마다 맞는 번호다', () => {
    const timeline = createSlideTimeline(1);
    timeline.change(10_000, 2);
    timeline.change(10_400, 3);
    timeline.change(25_000, 2);
    expect(timeline.at(9_999)).toBe(1);
    expect(timeline.at(10_200)).toBe(2);
    expect(timeline.at(10_400)).toBe(3);
    expect(timeline.at(24_999)).toBe(3);
    expect(timeline.at(25_000)).toBe(2);
  });

  it('새로고침해 이어받았으면 되살린 번호부터다 — 그 시각 앞도 같은 번호다', () => {
    const timeline = createSlideTimeline(4);
    expect(timeline.at(59_000)).toBe(4);
    timeline.change(61_000, 5);
    expect(timeline.at(60_500)).toBe(4);
    expect(timeline.at(61_000)).toBe(5);
  });
});
