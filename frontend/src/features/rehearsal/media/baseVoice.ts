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
 */
import type { VoiceRecord } from './voiceRecord';
import type { Ms } from '@/types/api';

/** 이만큼 말해야 기준으로 씁니다 (`volume.md` 5-2: 5초 미만이면 null) */
export const MIN_VOICED_MS = 5_000;

/** 이 시간 안에 다 말하지 못하면 그만둡니다. 문장 하나를 읽기에 넉넉한 시간입니다 */
export const MAX_READ_MS = 20_000;

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
