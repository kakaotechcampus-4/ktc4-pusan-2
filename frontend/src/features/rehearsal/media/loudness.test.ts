import { describe, expect, it } from 'vitest';
import { FAST_TAU_MS, createLoudness, meanSquareToDb } from './loudness';

/** rAF 한 번 간격(60fps) */
const FRAME_MS = 16;

const ms = (db: number) => 10 ** (db / 10);

/** dB 값을 durationMs 동안 프레임마다 넣습니다. 끝난 시각을 돌려줍니다 */
function feed(
  loudness: ReturnType<typeof createLoudness>,
  db: number,
  durationMs: number,
  startMs = 0,
): number {
  let t = startMs;
  for (; t < startMs + durationMs; t += FRAME_MS) loudness.update(db === -Infinity ? 0 : ms(db), t);
  return t;
}

describe('dB 변환', () => {
  it('평균 제곱 0.01 은 -20dB 다', () => {
    expect(meanSquareToDb(0.01)).toBeCloseTo(-20, 5);
  });

  it('0 은 바닥값이다 — log10(0) 이 나오지 않는다', () => {
    expect(meanSquareToDb(0)).toBe(-100);
  });
});

describe('Fast 시간 가중', () => {
  it('같은 크기가 이어지면 그 크기로 수렴한다', () => {
    const l = createLoudness();
    feed(l, -20, 1_000);
    expect(l.levelDb()).toBeCloseTo(-20, 1);
  });

  /** 소리가 끊기면 에너지가 e^(-t/τ) 로 줄어듭니다 — τ 만큼 지나면 약 4.3dB 아래 */
  it('소리가 끊기고 125ms 뒤에는 약 4.3dB 떨어져 있다', () => {
    const l = createLoudness();
    const next = feed(l, -20, 1_000);
    // feed 는 다음 프레임 시각을 돌려줍니다. 마지막으로 넣은 시각에서 τ 만큼 뒤를 봅니다
    l.update(0, next - FRAME_MS + FAST_TAU_MS);
    expect(l.levelDb()).toBeCloseTo(-20 - 10 * Math.log10(Math.E), 1);
  });
});

describe('말하는 구간만 평균', () => {
  /** 침묵을 섞어 평균하면 목소리가 실제보다 작게 나옵니다. 그걸 막는 게 이 기능입니다 */
  it('쉬는 시간이 섞여도 말한 크기로 나온다', () => {
    const l = createLoudness();
    let t = 0;
    for (let i = 0; i < 5; i++) {
      t = feed(l, -60, 2_000, t);
      t = feed(l, -20, 2_000, t);
    }

    const { leqDb, ms: spoke } = l.speech();
    expect(leqDb).not.toBeNull();
    expect(Math.abs(leqDb! - -20)).toBeLessThan(1);
    // 실제로 말한 시간은 10초입니다. 말을 멈춘 뒤 Fast 평균이 내려오는 동안(쉴 때마다
    // 약 0.35초)도 말하는 중으로 세서 조금 넘치지만, 쉰 시간 10초를 통째로 세지는 않습니다
    expect(spoke).toBeGreaterThan(9_000);
    expect(spoke).toBeLessThan(12_500);
  });

  it('잡음만 이어지면 말한 것으로 세지 않는다', () => {
    const l = createLoudness();
    feed(l, -50, 5_000);
    expect(l.speaking()).toBe(false);
    expect(l.speech()).toEqual({ leqDb: null, ms: 0 });
  });

  it('디지털 무음(0)만 들어오면 말한 것으로 세지 않는다', () => {
    const l = createLoudness();
    feed(l, -Infinity, 2_000);
    expect(l.speech().ms).toBe(0);
  });

  /** dB 를 그대로 평균내면 -25 가 나오지만, 소리 에너지로는 큰 쪽이 더 무겁습니다 */
  it('평균은 dB 가 아니라 에너지로 낸다', () => {
    const l = createLoudness();
    let t = feed(l, -60, 1_000);
    t = feed(l, -20, 2_000, t);
    feed(l, -30, 2_000, t);

    // 에너지 평균: 10·log10((0.01 + 0.001) / 2) ≈ -22.6
    expect(l.speech().leqDb!).toBeGreaterThan(-24);
  });

  /** 탭이 가려져 rAF 가 몇 초 멈췄다 돌아와도 그 공백을 말한 시간으로 세지 않습니다 */
  it('프레임 사이 공백은 최대 250ms 만 센다', () => {
    const l = createLoudness();
    let t = feed(l, -60, 1_000);
    t = feed(l, -20, 500, t);
    const before = l.speech().ms;

    l.update(ms(-20), t + 5_000);
    expect(l.speech().ms - before).toBeLessThanOrEqual(250);
  });
});
