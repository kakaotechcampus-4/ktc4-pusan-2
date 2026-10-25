/**
 * 음량 1초 기록 — WS `metrics.voice` 로 가는 값 (`docs/ai/architecture.md` 6-2, `volume.md` 5-2).
 *
 * Web Audio 를 모릅니다 — 계량기가 잰 레벨 · '말하는 중' 판정 · 시각만 받습니다.
 * 그래서 브라우저 없이 테스트할 수 있습니다. 넣는 쪽은 `useMicLevel` 입니다.
 *
 * ── 왜 1초마다 스스로 자르지 않나 ───────────────────────────────────
 * 음량 기록은 **시선 1초 기록과 같은 1초 격자(같은 `t_ms`)** 여야 합니다. 그런데 시선 격자는
 * 워커 안의 엔진이 첫 프레임 시각에서 시작합니다(`architecture.md` 6-1). 메인 스레드는 그 시각을
 * 미리 알 수 없어서, 레벨을 시간 순으로 쌓아 두었다가 **시선 기록이 덮는 구간을 그대로 받아**
 * 그 구간의 음량 기록을 만듭니다 (`record(startMs, endMs)`).
 *
 * ── 재는 법 (`volume.md` 5-2) ───────────────────────────────────────
 *   level_db     그 구간에서 '말하는 중'이었던 시간의 Leq. 에너지로 평균합니다(dB 를 평균하지 않음)
 *   voiced_ms    그 구간에서 '말하는 중'이었던 ms
 *   silence_ms   구간 끝에서 거꾸로 센 연속 '말하지 않음'. 앞 구간에서 이어진 무음도 더합니다
 *   audio_live   그 구간 동안 오디오가 흘렀나 — 오디오가 돌고 있고, 측정 간격이 상한을 넘은 공백이 없음
 *
 * '말하는 중'은 `loudness.ts` 의 한 기준(말소리 하한 · 잡음 바닥 여유)만 씁니다. 세 값이 서로 다른
 * 기준으로 재지면 판정 모듈이 '말한 1초'를 다르게 봅니다.
 */
import type { Ms } from '@/types/api';

/**
 * 측정 간격의 상한. 이보다 길게 비면 그 사이는 **재지 못한 공백**입니다 — 발화로도 침묵으로도
 * 세지 않고, 그 구간은 `audio_live: false` 입니다. 탭이 가려지면 rAF 가 멈췄다가 몇 초 뒤에
 * 한꺼번에 돌아옵니다. `loudness.ts` 의 `MAX_STEP_MS` 와 같은 값입니다 (`volume.md` 5-2: 지금 250 ms).
 */
export const MAX_GAP_MS = 250;

/**
 * 쌓아 두는 기간. 시선 기록은 1초마다 오고 늦어도 몇 초 안이라, 이보다 오래된 레벨은 다시 묻지 않습니다.
 * 침묵 길이는 따로 누적해 두므로 이 기간보다 긴 침묵도 셉니다.
 */
const KEEP_MS = 15_000;

/** 음량 1초 기록 하나. 필드 이름은 서버와 같은 snake_case 입니다 */
export interface VoiceRecord {
  t_ms: Ms;
  duration_ms: Ms;
  /** 말한 시간의 Leq(dBFS, A 가중 · Fast). 말한 시간이 없으면 null */
  level_db: number | null;
  voiced_ms: Ms;
  silence_ms: Ms;
  audio_live: boolean;
}

/** 계량기가 한 번 잰 값 */
export interface VoiceSample {
  /** 무대 시계(테이크 시작 기준 ms). 시선 기록과 같은 시계여야 같은 1초를 가리킵니다 */
  tMs: Ms;
  /** A 가중 · Fast 레벨(dBFS) */
  levelDb: number;
  /** '말하는 중' (`loudness.ts`) */
  speaking: boolean;
  /** 오디오가 흐르는 중 — AudioContext 가 running 이고 측정 트랙이 살아 있음 */
  live: boolean;
}

/** 직전 측정부터 이번 측정까지 `(tMs - stepMs, tMs]` 를 덮는 한 칸 */
interface Step {
  tMs: Ms;
  /** 직전 측정과의 실제 간격 */
  gapMs: Ms;
  /** 발화 · 침묵으로 센 시간. 공백이거나 오디오가 멈췄으면 0 */
  countedMs: Ms;
  /** 이 칸에서 말한 에너지 (평균 제곱 × ms). 말하지 않았으면 0 */
  energy: number;
  voiced: boolean;
  /** 공백이거나 오디오가 흐르지 않았음 */
  dead: boolean;
  /** 이 칸 끝에서 거꾸로 센 연속 침묵 */
  silenceRunMs: Ms;
}

export interface VoiceRecorder {
  push(sample: VoiceSample): void;
  /**
   * `[startMs, endMs)` 의 음량 기록. 그 구간 끝의 측정까지 쌓인 뒤에 부르세요 — 시선 기록이 나오는
   * 시점에는 그 구간의 마이크 측정이 이미 끝나 있습니다 (마이크는 rAF 마다, 시선은 프레임 뒤에 옵니다).
   */
  record(startMs: Ms, endMs: Ms): VoiceRecord;
  /** 쌓은 것을 비웁니다. 장치를 바꾸거나 시계가 처음부터 다시 갈 때 */
  reset(): void;
}

const round1 = (n: number) => Math.round(n * 10) / 10;

export function createVoiceRecorder(): VoiceRecorder {
  let steps: Step[] = [];
  let lastAt: Ms | null = null;

  return {
    push({ tMs, levelDb, speaking, live }) {
      // 시계가 뒤로 갔으면(새로고침해 이어받기 전 · 다른 Take) 앞 기록은 이 시간축의 것이 아닙니다
      if (lastAt !== null && tMs < lastAt) steps = [];
      const gapMs = lastAt === null || tMs < lastAt ? Infinity : tMs - lastAt;
      lastAt = tMs;

      const dead = !live || gapMs > MAX_GAP_MS;
      const countedMs = dead ? 0 : gapMs;
      const voiced = !dead && speaking;
      const prevRun = steps.at(-1)?.silenceRunMs ?? 0;

      steps.push({
        tMs,
        gapMs,
        countedMs,
        // 말할 때만 쌓습니다. 레벨은 Fast 가중 평균 제곱을 dB 로 바꾼 값이라 되돌리면 그 에너지입니다
        energy: voiced ? 10 ** (levelDb / 10) * countedMs : 0,
        voiced,
        dead,
        // 말하면 0 으로, 공백이면 끊습니다 — 공백은 침묵으로 누적하지 않습니다 (volume.md 5-2)
        silenceRunMs: voiced || dead ? 0 : prevRun + countedMs,
      });

      while (steps.length > 0 && tMs - steps[0]!.tMs > KEEP_MS) steps.shift();
    },

    record(startMs, endMs) {
      // 칸은 끝 시각이 속한 구간으로 셉니다. 경계에 걸친 칸은 한 프레임(약 16ms) 안쪽 차이입니다
      const inside = steps.filter((s) => s.tMs > startMs && s.tMs <= endMs);

      let voicedMs = 0;
      let energy = 0;
      let dead = inside.length === 0;
      for (const s of inside) {
        voicedMs += s.voiced ? s.countedMs : 0;
        energy += s.energy;
        if (s.dead) dead = true;
      }
      // 구간 끝까지 측정이 오지 않았으면 그 끝은 재지 못한 공백입니다
      const last = inside.at(-1);
      if (!last || endMs - last.tMs > MAX_GAP_MS) dead = true;

      return {
        t_ms: startMs,
        duration_ms: endMs - startMs,
        level_db: voicedMs > 0 ? round1(10 * Math.log10(energy / voicedMs)) : null,
        voiced_ms: Math.round(voicedMs),
        silence_ms: Math.round(last?.silenceRunMs ?? 0),
        audio_live: !dead,
      };
    },

    reset() {
      steps = [];
      lastAt = null;
    },
  };
}
