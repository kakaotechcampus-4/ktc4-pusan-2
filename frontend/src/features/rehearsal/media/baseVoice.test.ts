import { describe, expect, it } from 'vitest';
import { EMPTY_RUN, MIN_VOICED_MS, addRecord, baseLevelDb } from './baseVoice';
import type { VoiceRecord } from './voiceRecord';

const rec = (level_db: number | null, voiced_ms: number): VoiceRecord => ({
  t_ms: 0,
  duration_ms: 250,
  level_db,
  voiced_ms,
  silence_ms: 0,
  audio_live: true,
});

describe('평소 목소리 크기', () => {
  it(`말한 시간이 ${MIN_VOICED_MS}ms 미만이면 null 이다 — AI 가 Take 첫 발화로 잡는다`, () => {
    const run = addRecord(EMPTY_RUN, rec(-30, MIN_VOICED_MS - 1));
    expect(baseLevelDb(run)).toBeNull();
  });

  it('같은 크기로 읽으면 그 크기다', () => {
    let run = EMPTY_RUN;
    for (let i = 0; i < 24; i++) run = addRecord(run, rec(-28.5, 250));
    expect(run.voicedMs).toBe(6000);
    expect(baseLevelDb(run)).toBe(-28.5);
  });

  it('말하지 않은 구간은 더하지 않는다 — 쉼이 섞여도 작아지지 않는다', () => {
    let run = EMPTY_RUN;
    for (let i = 0; i < 24; i++) {
      run = addRecord(run, rec(-30, 250));
      run = addRecord(run, rec(null, 0));
    }
    expect(baseLevelDb(run)).toBe(-30);
  });

  it('크기는 에너지로 평균한다 — dB 를 평균하지 않는다', () => {
    let run = addRecord(EMPTY_RUN, rec(-20, 3000));
    run = addRecord(run, rec(-40, 3000));
    // dB 평균이면 -30, 에너지 평균은 약 -23.0
    expect(baseLevelDb(run)).toBeCloseTo(-23.0, 1);
  });
});
