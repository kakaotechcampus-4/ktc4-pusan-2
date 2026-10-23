/**
 * 평소 목소리 크기(기준 음량, `base_level_db`) — 발표 전 점검에서 잽니다 (`docs/ai/features/volume.md` 5-2).
 *
 * 마이크마다 · 방마다 같은 목소리의 dBFS 가 10dB 넘게 달라서, AI 는 "몇 dB 이하면 작다"가 아니라
 * **평소 목소리보다 얼마나 작은가**로 판정합니다. 그 평소 목소리를 여기서 잽니다.
 *
 * ── 재는 법 ─────────────────────────────────────────────────────────
 * 정해진 문장을 평소 목소리로 읽게 하고, **말한 구간만** Leq 로 냅니다. 발표 중 음량 1초 기록과
 * 같은 계량기 · 같은 '말하는 중' 기준(`voiceRecord.ts`)으로 재야 둘을 비교할 수 있습니다.
 * 읽는 동안 짧은 구간의 음량 기록을 받아 말한 시간 · 에너지를 더해 갑니다 — 읽는 시간이 길어져도
 * 앞부분을 잃지 않습니다.
 *
 * 말한 시간이 `MIN_VOICED_MS` 미만이면 null 입니다. 그러면 AI 가 Take 첫 발화로 기준을 잡습니다.
 *
 * ── 무엇을 확인하고 무엇을 못 하나 ──────────────────────────────────────
 * **말한 내용은 보지 않습니다.** 음성 인식(STT)은 Take 를 만든 뒤에야 쓸 수 있습니다 (WS 가 Take 에 붙음).
 * 대신 "문장을 끝까지, 끊기지 않고 읽은 소리"인지를 소리만으로 봅니다 (`judgeRead`) —
 *   끝까지   말한 시간을 채운 뒤 **말이 멈출 때까지** 기다립니다. 읽는 도중에 끝내지 않습니다
 *   끊김 없이  처음 말한 때부터 마지막 말한 때까지 중 말한 비율이 너무 낮으면(기침 · 띄엄띄엄) 다시 읽게 합니다
 */
import type { VoiceRecord } from './voiceRecord';
import type { Ms } from '@/types/api';

/** 이만큼 말해야 기준으로 씁니다 (`volume.md` 5-2: 5초 미만이면 null) */
export const MIN_VOICED_MS = 5_000;

/** 이 시간이 지나면 그만둡니다. 준비한 문장을 모두 이어 읽어도 넉넉한 시간입니다 */
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
 * 읽기를 끝까지 못 채웠을 때 그 이유.
 *
 *   QUIET  말소리로 거의 들리지 않음 — 목소리가 작거나 주변이 시끄러움. "더 길게"가 아니라 마이크에 가까이
 *   BRIEF  들리긴 했는데 말한 시간이 모자람 — 쉬는 시간이 길었음
 *   CHOPPY 시간은 채웠지만 말한 사이사이가 너무 비었음 — 기침 · 잡음 · 띄엄띄엄 읽기
 *
 * 둘을 나누는 이유: '말하는 중'은 주변 소음보다 충분히 커야 잡힙니다 (`loudness.ts`).
 * 작게 말해서 안 잡힌 사람에게 "더 길게 읽어 주세요"라고 하면 같은 크기로 다시 읽어 또 실패합니다.
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
 *   ENOUGH   잴 만큼 들었음 — 끝까지 읽고 멈추면 끝남
 */
export type ReadStage = 'WAITING' | 'HEARD' | 'ENOUGH';

export function readStage(run: BaseVoiceRun): ReadStage {
  if (run.voicedMs >= MIN_VOICED_MS) return 'ENOUGH';
  return run.voicedMs > 0 ? 'HEARD' : 'WAITING';
}

/** 말한 시간을 채운 뒤 이만큼 조용하면 다 읽은 것으로 봅니다 */
export const END_SILENCE_MS = 1_000;

/**
 * 말한 시간을 채우기 전에 이만큼 조용하면, 실패로 끝내지 않고 다음 문장을 보여 줘 이어 읽게 합니다.
 *
 * '말하는 중'은 주변 소음보다 충분히 큰 소리만 셉니다 (`loudness.ts`). 글자 사이 · 구절 끝 · 숨 쉬는 틈이
 * 빠져서, 실제로 읽은 시간의 절반 남짓만 잡힙니다. 문장 하나를 다 읽어도 5초가 안 될 수 있습니다.
 */
export const MORE_AFTER_SILENCE_MS = 1_500;

/**
 * 처음 말한 때부터 마지막 말한 때까지 중 말한 비율의 하한. 위처럼 실제 읽기의 절반 남짓만 잡히고,
 * 이어 읽기 사이의 쉼도 들어가므로 낮게 둡니다. 잠정값입니다 — 실제 읽기로 재서 조정합니다.
 */
export const MIN_VOICED_RATIO = 0.2;

export interface ReadState {
  run: BaseVoiceRun;
  startedAt: Ms;
  /** 처음 · 마지막으로 말소리가 들린 때. 아직 안 들렸으면 null */
  firstVoiceAt: Ms | null;
  lastVoiceAt: Ms | null;
  /** 보여 준 문장 수. 처음 1, 이어 읽기를 청할 때마다 하나씩 늡니다 */
  sentences: number;
  /** 마지막으로 이어 읽기를 청한 때. 그 뒤로 다시 조용해야 또 청합니다 */
  askedAt: Ms | null;
}

export const startRead = (now: Ms): ReadState => ({
  run: EMPTY_RUN,
  startedAt: now,
  firstVoiceAt: null,
  lastVoiceAt: null,
  sentences: 1,
  askedAt: null,
});

/** 다음 문장을 보여 주며 이어 읽기를 청합니다 */
export const askMore = (state: ReadState, now: Ms): ReadState => ({
  ...state,
  sentences: state.sentences + 1,
  askedAt: now,
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

export type ReadVerdict =
  | { kind: 'READING' }
  | { kind: 'MORE' }
  | { kind: 'DONE'; levelDb: number; heardMs: Ms }
  | { kind: 'SHORT'; reason: ShortReason };

/**
 * 지금 끝낼지. 말한 시간을 채웠어도 **말이 멈출 때까지** 기다립니다 — 문장 전체가 들어가야 하고,
 * 읽는 도중에 "확인됨"이 뜨면 사용자는 측정이 끊겼다고 느낍니다. 다만 최대 시간이 지나면 끝냅니다.
 *
 * 채우기 전에 멈췄으면 **실패가 아니라 이어 읽기**입니다(`MORE`) — 문장을 다 읽었는데 "모자랐어요"가
 * 뜨면 사용자는 무엇을 잘못했는지 알 수 없습니다. 준비한 문장(`totalSentences`)을 다 쓰고도 모자랄 때만 끝냅니다.
 */
export function judgeRead(state: ReadState, now: Ms, totalSentences: number): ReadVerdict {
  const { run, startedAt, firstVoiceAt, lastVoiceAt, sentences, askedAt } = state;
  const timedOut = now - startedAt > MAX_READ_MS;
  const enough = run.voicedMs >= MIN_VOICED_MS;

  if (enough && lastVoiceAt !== null && firstVoiceAt !== null) {
    const stopped = now - lastVoiceAt >= END_SILENCE_MS;
    if (!stopped && !timedOut) return { kind: 'READING' };
    const span = Math.max(1, lastVoiceAt - firstVoiceAt);
    if (run.voicedMs / span < MIN_VOICED_RATIO) return { kind: 'SHORT', reason: 'CHOPPY' };
    return { kind: 'DONE', levelDb: baseLevelDb(run)!, heardMs: run.voicedMs };
  }
  if (timedOut) return { kind: 'SHORT', reason: shortReason(run) };
  // 들리다가 멈췄습니다 — 마지막 소리 뒤로도, 마지막으로 청한 뒤로도 조용해야 합니다
  const quietSince = Math.max(lastVoiceAt ?? Infinity, askedAt ?? -Infinity);
  if (lastVoiceAt !== null && now - quietSince >= MORE_AFTER_SILENCE_MS) {
    if (sentences < totalSentences) return { kind: 'MORE' };
    return { kind: 'SHORT', reason: shortReason(run) };
  }
  return { kind: 'READING' };
}
