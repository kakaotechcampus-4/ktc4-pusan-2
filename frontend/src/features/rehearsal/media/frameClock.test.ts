import { describe, expect, it } from 'vitest';
import { ANALYSIS_FPS, nextFrameDue } from './frameClock';

const INTERVAL = 1000 / ANALYSIS_FPS;

/** 60Hz rAF 를 흉내 냅니다 — 16.7ms 마다 한 번, 예정 시각이 지났으면 보냅니다 */
function simulate(durationMs: number): number {
  let due = 0;
  let sent = 0;
  for (let now = 0; now < durationMs; now += 1000 / 60) {
    if (now < due) continue;
    sent++;
    due = nextFrameDue(due, now, INTERVAL);
  }
  return sent;
}

describe('분석 프레임 속도', () => {
  it('60Hz 화면에서도 평균 초당 8장을 지킨다 — rAF 지연만큼 밀리지 않는다', () => {
    // 10초에 80장. 밀림이 쌓이면 75장 근처로 떨어집니다
    expect(simulate(10_000)).toBeGreaterThanOrEqual(79);
    expect(simulate(10_000)).toBeLessThanOrEqual(81);
  });

  it('제 시각에 보냈으면 직전 예정 시각에 간격을 더한다', () => {
    expect(nextFrameDue(125, 133, INTERVAL)).toBe(250);
  });

  it('한 간격 넘게 뒤처졌으면 지금부터 다시 잡는다 — 몰아서 보내지 않는다', () => {
    // 125ms 예정이던 것을 2초 뒤에야 보냈으면 다음은 2초 + 한 간격
    expect(nextFrameDue(125, 2000, INTERVAL)).toBe(2000 + INTERVAL);
  });
});
