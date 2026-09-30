/**
 * A-가중 필터 계수. 소음계가 보여 주는 dB(A)의 그 A 입니다.
 *
 * ── 왜 필요한가 ─────────────────────────────────────────────────────
 * 사람 귀는 아주 낮은 소리(에어컨 웅웅거림)와 아주 높은 소리를 덜 크게 느낍니다.
 * 필터 없이 에너지를 그대로 재면 저음 잡음이 목소리 크기에 섞이고,
 * 소음계 앱과 비교할 때도 몇 dB씩 어긋납니다.
 *
 * ── 어떻게 만들었나 ─────────────────────────────────────────────────
 * IEC 61672 의 아날로그 식을 쌍선형 변환으로 디지털로 옮겼습니다.
 *
 *   H(s) = K · ω4² · s⁴ / ((s+ω1)² (s+ω2)(s+ω3) (s+ω4)²)
 *
 * 6차 필터 하나로 두지 않고 **2차 셋으로 나눕니다.** 20Hz 극점이 단위원에 아주
 * 가까워서, 한 덩어리로 두면 계수 오차가 그대로 불안정으로 번집니다.
 *
 * 고음 쪽(10kHz 이상)은 쌍선형 변환 때문에 표준보다 조금 더 깎입니다.
 * 말소리 에너지는 거의 4kHz 아래라 목소리 크기에는 영향이 없습니다.
 */

/** IEC 61672 의 극점 주파수(Hz) */
const F1 = 20.598997;
const F2 = 107.65265;
const F3 = 737.86223;
const F4 = 12194.217;

/** IIRFilterNode 하나에 들어가는 계수. feedback[0] 은 1 로 맞춰 둡니다 */
export interface IirSection {
  feedforward: number[];
  feedback: number[];
}

/** 아날로그 2차식 (b2 s² + b1 s + b0) / (a2 s² + a1 s + a0) 을 쌍선형 변환합니다 */
function bilinear(
  [b2, b1, b0]: [number, number, number],
  [a2, a1, a0]: [number, number, number],
  sampleRate: number,
): IirSection {
  const k = 2 * sampleRate;
  const k2 = k * k;
  const a = [a2 * k2 + a1 * k + a0, 2 * a0 - 2 * a2 * k2, a2 * k2 - a1 * k + a0];
  const b = [b2 * k2 + b1 * k + b0, 2 * b0 - 2 * b2 * k2, b2 * k2 - b1 * k + b0];
  const norm = a[0]!;
  return { feedforward: b.map((v) => v / norm), feedback: a.map((v) => v / norm) };
}

/** 한 구간의 주파수 응답 크기(배율) */
function sectionGain({ feedforward, feedback }: IirSection, freq: number, sampleRate: number) {
  const w = (2 * Math.PI * freq) / sampleRate;
  const eval_ = (c: number[]) => {
    let re = 0;
    let im = 0;
    c.forEach((v, n) => {
      re += v * Math.cos(w * n);
      im -= v * Math.sin(w * n);
    });
    return Math.hypot(re, im);
  };
  return eval_(feedforward) / eval_(feedback);
}

/** 구간들을 이어 붙였을 때 freq 에서의 응답(dB). 검증용입니다 */
export function responseDb(sections: IirSection[], freq: number, sampleRate: number): number {
  const gain = sections.reduce((g, s) => g * sectionGain(s, freq, sampleRate), 1);
  return 20 * Math.log10(gain);
}

/**
 * 이 샘플레이트에서 쓸 A-가중 필터 세 구간.
 *
 * 1kHz 에서 0dB 가 되도록 첫 구간에 배율을 접어 넣습니다 — 표준의 정의가 그렇고,
 * 그래야 말소리 대역의 값이 필터 전과 크게 달라지지 않습니다.
 */
export function aWeightingSections(sampleRate: number): IirSection[] {
  const w1 = 2 * Math.PI * F1;
  const w2 = 2 * Math.PI * F2;
  const w3 = 2 * Math.PI * F3;
  const w4 = 2 * Math.PI * F4;

  const sections = [
    // s² / (s+ω1)²  — 20Hz 아래를 깎는 고역 통과
    bilinear([1, 0, 0], [1, 2 * w1, w1 * w1], sampleRate),
    // s² / ((s+ω2)(s+ω3))  — 저음을 서서히 깎는 부분
    bilinear([1, 0, 0], [1, w2 + w3, w2 * w3], sampleRate),
    // ω4² / (s+ω4)²  — 12kHz 위를 깎는 저역 통과
    bilinear([0, 0, w4 * w4], [1, 2 * w4, w4 * w4], sampleRate),
  ];

  const at1k = 10 ** (responseDb(sections, 1000, sampleRate) / 20);
  const first = sections[0]!;
  sections[0] = { ...first, feedforward: first.feedforward.map((v) => v / at1k) };
  return sections;
}
