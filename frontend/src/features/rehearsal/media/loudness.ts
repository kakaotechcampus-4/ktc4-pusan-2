/**
 * 목소리 크기 계산. 소음계와 같은 방식으로 **시간 평균**을 내고,
 * **말하는 구간만** 따로 모아 평균을 냅니다.
 *
 * Web Audio 를 모릅니다 — 한 블록의 평균 제곱(mean square)과 시각만 받습니다.
 * 그래서 브라우저 없이 테스트할 수 있습니다. 읽는 쪽은 `level.ts` 입니다.
 *
 * ── 단위 ────────────────────────────────────────────────────────────
 * 전부 dBFS 입니다 (마이크가 받을 수 있는 최대치 = 0dB).
 * 실제 소리 크기(dB SPL)로 바꾸려면 기기별 보정값이 필요한데, 그건 아직 정하지
 * 않았습니다. 여기 숫자에 무엇을 더할지는 이 파일이 아니라 보여 주는 쪽이 정합니다.
 */

/**
 * 소음계의 "Fast" 시간 가중 (IEC 61672). 말소리 크기를 볼 때 쓰는 값입니다.
 * 더 느리게(Slow, 1초) 하면 문장 사이의 쉼이 평균에 묻힙니다.
 */
export const FAST_TAU_MS = 125;

/** 이보다 작으면 계산상 무음으로 봅니다. log10(0) 을 피하기 위한 바닥입니다 */
export const FLOOR_DB = -100;

/** 잡음 바닥보다 이만큼 크면 말하는 중으로 봅니다 */
const SPEECH_MARGIN_DB = 10;

/** 잡음 바닥과 상관없이 이보다 작으면 말소리가 아닙니다. 디지털 무음(0) 직후를 거릅니다 */
const SPEECH_MIN_DB = -65;

/**
 * 잡음 바닥이 올라가는 속도. 내려갈 때는 바로 따라갑니다.
 *
 * 빨리 올리면 계속 말하는 동안 바닥이 목소리를 따라 올라와서 말소리가 잡음으로
 * 바뀝니다. 말소리는 구절 사이에 0.2~0.5초씩 쉬고, Fast 가중으로 0.5초면 약 17dB
 * 떨어지므로 그때마다 바닥이 다시 내려옵니다.
 */
const NOISE_RISE_TAU_MS = 5_000;

/**
 * 한 번에 쌓는 시간의 상한. 탭이 가려지면 rAF 가 멈췄다가 몇 초 뒤에 한 번에
 * 돌아오는데, 그 공백을 말한 시간으로 세면 평균이 틀어집니다.
 */
const MAX_STEP_MS = 250;

export function meanSquareToDb(meanSquare: number): number {
  if (meanSquare <= 0) return FLOOR_DB;
  return Math.max(FLOOR_DB, 10 * Math.log10(meanSquare));
}

export interface SpeechLevel {
  /** 말한 구간의 등가 소음도(Leq). 아직 말한 적이 없으면 null */
  leqDb: number | null;
  /** 말한 것으로 센 시간 */
  ms: number;
}

export interface Loudness {
  /** 새 블록을 넣고 Fast 가중 레벨(dBFS)을 돌려받습니다 */
  update(blockMeanSquare: number, nowMs: number): number;
  /** 마지막 Fast 가중 레벨 */
  levelDb(): number;
  /** 지금 말하는 중인가 */
  speaking(): boolean;
  speech(): SpeechLevel;
}

export function createLoudness(): Loudness {
  let meanSquare = 0;
  let level = FLOOR_DB;
  let lastAt: number | null = null;
  let noiseDb: number | null = null;
  let isSpeaking = false;
  let speechEnergy = 0;
  let speechMs = 0;

  return {
    update(blockMeanSquare, nowMs) {
      // 첫 블록은 평균할 과거가 없으니 그 값에서 시작합니다
      const dt = lastAt === null ? Infinity : Math.max(0, nowMs - lastAt);
      lastAt = nowMs;

      const alpha = 1 - Math.exp(-dt / FAST_TAU_MS);
      meanSquare += (blockMeanSquare - meanSquare) * alpha;
      level = meanSquareToDb(meanSquare);

      if (noiseDb === null || level < noiseDb) {
        noiseDb = level;
      } else {
        noiseDb += (level - noiseDb) * (1 - Math.exp(-dt / NOISE_RISE_TAU_MS));
      }

      isSpeaking = level > SPEECH_MIN_DB && level > noiseDb + SPEECH_MARGIN_DB;
      if (isSpeaking && Number.isFinite(dt)) {
        const step = Math.min(dt, MAX_STEP_MS);
        // dB 가 아니라 에너지로 쌓습니다 — dB 를 평균내면 작은 소리 쪽으로 치우칩니다
        speechEnergy += meanSquare * step;
        speechMs += step;
      }
      return level;
    },

    levelDb: () => level,

    speaking: () => isSpeaking,

    speech: () => ({
      leqDb: speechMs > 0 ? meanSquareToDb(speechEnergy / speechMs) : null,
      ms: Math.round(speechMs),
    }),
  };
}
