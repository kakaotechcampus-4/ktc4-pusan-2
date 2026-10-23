import { describe, expect, it } from 'vitest';
import { GAZE_WAIT_MS, SLICE_MS, createMetricsClock, type MetricsWindow } from './metricsClock';
import type { GazeSampleRecord } from '@/workers/gaze.contract';

const g = (t_ms: number, duration_ms = SLICE_MS): GazeSampleRecord => ({
  t_ms,
  duration_ms,
  state: 'CAMERA',
  direction: null,
  confidence: 0.9,
  reliability: 0.95,
  issues: [],
  frames: 8,
});

/** 구간을 [시작, 끝, 시선 있음] 으로 줄여 봅니다 */
const brief = (windows: MetricsWindow[]) =>
  windows.map((w) => [w.startMs, w.endMs, w.gaze !== null] as const);

describe('시선 기록이 오면', () => {
  it('그 구간을 그대로 보낸다 — 1초 격자가 1000 의 배수가 아니어도', () => {
    const clock = createMetricsClock(59_390);
    expect(brief(clock.gaze([g(59_390), g(60_390)]))).toEqual([
      [59_390, 60_390, true],
      [60_390, 61_390, true],
    ]);
  });

  it('엔진이 늦게 떴으면 그 앞 시간을 음량만으로 메워 시선 격자에 맞춘다', () => {
    const clock = createMetricsClock(0);
    // 엔진 첫 프레임이 2.42초 — 그 앞 2.42초는 시선 없이 보냅니다
    expect(brief(clock.gaze([g(2_420)]))).toEqual([
      [0, 1_000, false],
      [1_000, 2_000, false],
      [2_000, 2_420, false],
      [2_420, 3_420, true],
    ]);
  });

  it('이미 보낸 구간과 겹치는 늦은 시선 기록은 보내지 않는다 — 같은 시간을 두 번 세지 않게', () => {
    const clock = createMetricsClock(0);
    clock.gaze([g(0)]);
    // 시선이 끊겨 스스로 1초를 이었습니다
    expect(brief(clock.tick(1_000 + SLICE_MS + GAZE_WAIT_MS))).toEqual([[1_000, 2_000, false]]);
    // 그 1초의 시선 기록이 아주 늦게 왔습니다
    expect(clock.gaze([g(1_000)])).toEqual([]);
    // 다음 1초부터는 다시 시선 기록으로 보냅니다
    expect(brief(clock.gaze([g(2_000)]))).toEqual([[2_000, 3_000, true]]);
  });
});

describe('시선 기록이 오지 않으면', () => {
  it(`${GAZE_WAIT_MS}ms 를 기다린 뒤에야 스스로 1초를 이어 보낸다`, () => {
    const clock = createMetricsClock(0);
    expect(clock.tick(SLICE_MS + GAZE_WAIT_MS - 1)).toEqual([]);
    expect(brief(clock.tick(SLICE_MS + GAZE_WAIT_MS))).toEqual([[0, 1_000, false]]);
  });

  it('카메라가 끊겨 시선이 멈추면 마지막 시선 격자에 이어 보낸다', () => {
    const clock = createMetricsClock(0);
    clock.gaze([g(420), g(1_420)]);
    expect(brief(clock.tick(2_420 + 2 * SLICE_MS + GAZE_WAIT_MS))).toEqual([
      [2_420, 3_420, false],
      [3_420, 4_420, false],
    ]);
  });

  it('탭이 가려졌다 돌아오면 밀린 1초를 한꺼번에 낸다', () => {
    const clock = createMetricsClock(0);
    expect(clock.tick(5 * SLICE_MS + GAZE_WAIT_MS)).toHaveLength(5);
  });
});

describe('발표 끝', () => {
  it('안 보낸 시간을 끝 시각까지 메운다 — 마지막 조각은 짧다', () => {
    const clock = createMetricsClock(0);
    clock.gaze([g(0), g(1_000)]);
    expect(brief(clock.finish(3_400))).toEqual([
      [2_000, 3_000, false],
      [3_000, 3_400, false],
    ]);
  });

  it('시선 마지막 조각이 끝까지 닫혔으면 더 보낼 것이 없다', () => {
    const clock = createMetricsClock(0);
    clock.gaze([g(0), g(1_000, 400)]);
    expect(clock.finish(1_400)).toEqual([]);
  });

  it('끝난 뒤에는 아무것도 내지 않는다', () => {
    const clock = createMetricsClock(0);
    clock.finish(500);
    expect(clock.gaze([g(500)])).toEqual([]);
    expect(clock.tick(99_000)).toEqual([]);
    expect(clock.finish(9_000)).toEqual([]);
  });

  it('새로고침해 이어받았으면 그 시각부터 보낸다', () => {
    const clock = createMetricsClock(42_000);
    expect(brief(clock.tick(43_000 + GAZE_WAIT_MS))).toEqual([[42_000, 43_000, false]]);
  });
});
