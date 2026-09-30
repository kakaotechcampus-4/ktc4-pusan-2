import { describe, expect, it } from 'vitest';
import { aWeightingSections, responseDb } from './aWeighting';

/**
 * IEC 61672 표의 A-가중 값(dB). 소음계 앱과 같은 기준인지를 이 표로 봅니다.
 * 말소리 대역(4kHz 이하)은 0.2dB 안에 들어야 합니다.
 */
const STANDARD: [number, number][] = [
  [31.5, -39.4],
  [63, -26.2],
  [100, -19.1],
  [250, -8.6],
  [500, -3.2],
  [1000, 0],
  [2000, 1.2],
  [4000, 1.0],
];

describe.each([44100, 48000])('A-가중 필터 (%iHz)', (sampleRate) => {
  const sections = aWeightingSections(sampleRate);

  it.each(STANDARD)('%fHz 는 표준값 %fdB 에 맞는다', (freq, expected) => {
    expect(Math.abs(responseDb(sections, freq, sampleRate) - expected)).toBeLessThan(0.2);
  });

  /** 쌍선형 변환 때문에 고음은 표준(-1.1dB)보다 조금 더 깎입니다. 말소리에는 영향이 없습니다 */
  it('8kHz 는 1dB 안쪽으로만 어긋난다', () => {
    expect(Math.abs(responseDb(sections, 8000, sampleRate) - -1.1)).toBeLessThan(1);
  });

  /** IIRFilterNode 는 feedback[0] 이 0 이면 만들어지지 않습니다 */
  it('세 구간 모두 2차이고 feedback[0] 이 1 이다', () => {
    expect(sections).toHaveLength(3);
    for (const s of sections) {
      expect(s.feedforward).toHaveLength(3);
      expect(s.feedback).toHaveLength(3);
      expect(s.feedback[0]).toBe(1);
    }
  });
});
