/**
 * 평소 목소리 크기(기준 음량, `base_level_db`) — 발표 전 점검에서 잽니다 (`docs/ai/features/volume.md` 5-2).
 *
 * 마이크마다 · 방마다 같은 목소리의 dBFS 가 10dB 넘게 달라서, AI 는 "몇 dB 이하면 작다"가 아니라
 * **평소 목소리보다 얼마나 작은가**로 판정합니다. 그 평소 목소리를 여기서 잽니다.
 *
 * ── 재는 법 ─────────────────────────────────────────────────────────
 * 정해진 문단을 평소 목소리로 읽게 하고, **말한 구간만** Leq 로 냅니다. 발표 중 음량 1초 기록과
 * 같은 계량기 · 같은 '말하는 중' 기준(`voiceRecord.ts`)으로 재야 둘을 비교할 수 있습니다.
 * 읽는 동안 짧은 구간의 음량 기록을 받아 말한 시간 · 에너지를 더해 갑니다 — 읽는 시간이 길어져도
 * 앞부분을 잃지 않습니다.
 *
 * 말한 시간이 `MIN_VOICED_MS` 미만이면 null 입니다. 그러면 AI 가 Take 첫 발화로 기준을 잡습니다.
 *
 * ── 끝은 사용자가 알립니다 ───────────────────────────────────────────
 * **말한 내용은 보지 않습니다.** 음성 인식(STT)은 Take 를 만든 뒤에야 쓸 수 있습니다 (WS 가 Take 에 붙음).
 * 그리고 '말하는 중'은 주변 소음보다 충분히 큰 소리만 세서(`loudness.ts`), 글자 사이 · 구절 끝 · 숨 쉬는
 * 틈이 빠지고 읽는 중에도 1초 넘게 끊겨 보입니다. 그래서 "말이 멈췄다 = 다 읽었다"를 소리로 정하면
 * 읽는 도중에 끝나거나 문장을 더 붙이게 됩니다. 문단을 한 번에 보여 주고, 다 읽으면 사용자가
 * "다 읽었어요"를 누릅니다 (`finishRead`). 누르지 않으면 `MAX_READ_MS` 에 끝냅니다.
 */
import type { VoiceRecord } from './voiceRecord';
import type { Ms } from '@/types/api';

/** 이만큼 말해야 기준으로 씁니다 (`volume.md` 5-2: 5초 미만이면 null) */
export const MIN_VOICED_MS = 5_000;

/** 누르지 않으면 이 시간에 끝냅니다. 문단을 천천히 읽어도 넉넉한 시간입니다 */
export const MAX_READ_MS = 45_000;

export interface BaseVoiceRun {
  voicedMs: Ms;
  /** 말한 시간의 에너지 합 (평균 제곱 × ms) */
  energy: number;
}

export const EMPTY_RUN: BaseVoiceRun = { voicedMs: 0, energy: 0 };

/** 짧은 구간의 음량 기록 하나를 더합니다. 크기는 에너지로 더해야 평균이 맞습니다 (dB 를 더하지 않음) */
export function addRecord(run: BaseVoiceRun, rec: VoiceRecord): BaseVoiceRun {
  if (rec.level_db === null || rec.voiced_ms === 0) return run;
  return {
    voicedMs: run.voicedMs + rec.voiced_ms,
    energy: run.energy + 10 ** (rec.level_db / 10) * rec.voiced_ms,
  };
}

/** 모은 것의 Leq(dBFS, 소수 한 자리). 말한 시간이 모자라면 null */
export function baseLevelDb(run: BaseVoiceRun): number | null {
  if (run.voicedMs < MIN_VOICED_MS) return null;
  return Math.round(10 * Math.log10(run.energy / run.voicedMs) * 10) / 10;
}

/**
 * 다시 읽어야 하는 이유.
 *
 *   QUIET  말소리로 거의 들리지 않음 — 목소리가 작거나 주변이 시끄러움. "더 길게"가 아니라 마이크에 가까이
 *   BRIEF  들리긴 했는데 말한 시간이 모자람 — 끝까지 읽기 전에 눌렀거나 쉬는 시간이 길었음
 *   CHOPPY 시간은 채웠지만 말한 사이사이가 너무 비었음 — 기침 · 잡음 · 띄엄띄엄 읽기
 *
 * 나누는 이유: 작게 말해서 안 잡힌 사람에게 "더 길게 읽어 주세요"라고 하면 같은 크기로 다시 읽어 또 실패합니다.
 */
export type ShortReason = 'QUIET' | 'BRIEF' | 'CHOPPY';

/** 이보다 적게 들렸으면 말소리를 거의 못 잡은 것으로 봅니다 */
export const QUIET_BELOW_MS = 1_500;

export const shortReason = (run: BaseVoiceRun): ShortReason =>
  run.voicedMs < QUIET_BELOW_MS ? 'QUIET' : 'BRIEF';

/**
 * 읽는 동안 화면이 보여 줄 단계. 숫자 · 시간 막대 대신 "지금 무엇을 기다리나"를 보여 줍니다 —
 * "들은 시간 2.5초"는 사용자가 잘 되고 있는지 판단할 근거가 되지 못했습니다.
 *
 *   WAITING  아직 말소리가 안 들림
 *   HEARD    들리고 있음 — 계속 읽으면 됨
 *   ENOUGH   잴 만큼 들었음 — 끝까지 읽고 "다 읽었어요"를 누르면 됨
 */
export type ReadStage = 'WAITING' | 'HEARD' | 'ENOUGH';

export function readStage(run: BaseVoiceRun): ReadStage {
  if (run.voicedMs >= MIN_VOICED_MS) return 'ENOUGH';
  return run.voicedMs > 0 ? 'HEARD' : 'WAITING';
}

/**
 * 처음 말한 때부터 마지막 말한 때까지 중 말한 비율의 하한. '말하는 중'은 실제 읽기의 절반 남짓만 잡혀서
 * 낮게 둡니다 — 이보다 낮으면 문단을 읽은 소리가 아니라 기침 · 잡음이 띄엄띄엄 들린 것입니다.
 * 잠정값입니다 — 실제 읽기로 재서 조정합니다.
 */
export const MIN_VOICED_RATIO = 0.2;

export interface ReadState {
  run: BaseVoiceRun;
  startedAt: Ms;
  /** 처음 · 마지막으로 말소리가 들린 때. 아직 안 들렸으면 null */
  firstVoiceAt: Ms | null;
  lastVoiceAt: Ms | null;
}

export const startRead = (now: Ms): ReadState => ({
  run: EMPTY_RUN,
  startedAt: now,
  firstVoiceAt: null,
  lastVoiceAt: null,
});

/** 짧은 구간 `[fromMs, now)` 의 음량 기록을 더합니다 */
export function stepRead(state: ReadState, rec: VoiceRecord, fromMs: Ms, now: Ms): ReadState {
  const heard = rec.voiced_ms > 0;
  return {
    ...state,
    run: addRecord(state.run, rec),
    firstVoiceAt: heard ? (state.firstVoiceAt ?? fromMs) : state.firstVoiceAt,
    lastVoiceAt: heard ? now : state.lastVoiceAt,
  };
}

export type ReadResult =
  { kind: 'DONE'; levelDb: number; heardMs: Ms } | { kind: 'SHORT'; reason: ShortReason };

/**
 * 읽기를 끝냅니다 — "다 읽었어요"를 눌렀거나(`byUser`) 최대 시간이 지났을 때.
 *
 * 모자랄 때의 이유는 끝낸 쪽에 따라 다릅니다. 사용자가 일찍 눌렀으면 조금이라도 들린 이상 "안 들림"이
 * 아니라 "모자람"입니다 — 목소리가 작다고 하면 다음에 일부러 크게 읽습니다. 최대 시간까지 읽었는데
 * 거의 안 잡혔으면 그때는 정말 안 들린 것입니다.
 */
export function finishRead(state: ReadState, byUser: boolean): ReadResult {
  const { run, firstVoiceAt, lastVoiceAt } = state;
  const levelDb = baseLevelDb(run);
  if (levelDb === null || firstVoiceAt === null || lastVoiceAt === null) {
    if (byUser) return { kind: 'SHORT', reason: run.voicedMs > 0 ? 'BRIEF' : 'QUIET' };
    return { kind: 'SHORT', reason: shortReason(run) };
  }
  const span = Math.max(1, lastVoiceAt - firstVoiceAt);
  if (run.voicedMs / span < MIN_VOICED_RATIO) return { kind: 'SHORT', reason: 'CHOPPY' };
  return { kind: 'DONE', levelDb, heardMs: run.voicedMs };
}

/** 최대 시간이 지났나. 지났으면 `finishRead` 로 끝냅니다 */
export const readTimedOut = (state: ReadState, now: Ms): boolean =>
  now - state.startedAt > MAX_READ_MS;
