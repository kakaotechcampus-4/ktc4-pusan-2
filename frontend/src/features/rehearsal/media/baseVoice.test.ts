import { describe, expect, it } from 'vitest';
import {
  EMPTY_RUN,
  MIN_VOICED_MS,
  QUIET_BELOW_MS,
  addRecord,
  baseLevelDb,
  MAX_READ_MS,
  finishRead,
  readStage,
  readTimedOut,
  shortReason,
  startRead,
  stepRead,
  type ReadState,
} from './baseVoice';
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

describe('읽는 동안 보여 줄 것', () => {
  it('단계는 안 들림 → 들림 → 충분히 들음 순서다', () => {
    expect(readStage(EMPTY_RUN)).toBe('WAITING');
    expect(readStage(addRecord(EMPTY_RUN, rec(-30, 250)))).toBe('HEARD');
    expect(readStage(addRecord(EMPTY_RUN, rec(-30, MIN_VOICED_MS)))).toBe('ENOUGH');
  });

  it('말소리로 거의 안 들렸으면 QUIET — "더 길게"가 아니라 마이크에 가까이', () => {
    expect(shortReason(addRecord(EMPTY_RUN, rec(-30, QUIET_BELOW_MS - 1)))).toBe('QUIET');
  });

  it('들리긴 했는데 모자라면 BRIEF', () => {
    expect(shortReason(addRecord(EMPTY_RUN, rec(-30, 3000)))).toBe('BRIEF');
  });
});

/** 0.25초씩 `pattern` 대로 읽습니다. true 는 말한 구간, false 는 쉰 구간 */
function read(pattern: boolean[], level = -30): { state: ReadState; now: number } {
  let state = startRead(0);
  let now = 0;
  for (const voiced of pattern) {
    now += 250;
    state = stepRead(state, rec(voiced ? level : null, voiced ? 250 : 0));
  }
  return { state, now };
}
const talk = (ms: number) => Array<boolean>(ms / 250).fill(true);
const pause = (ms: number) => Array<boolean>(ms / 250).fill(false);

describe('다 읽었어요를 누르면 — 내용은 보지 않고 소리로만', () => {
  it('충분히 말했으면 들은 시간과 크기를 낸다', () => {
    const { state } = read(talk(7000));
    expect(finishRead(state, true)).toEqual({ kind: 'DONE', levelDb: -30, heardMs: 7000 });
  });

  it('문장 사이 쉼은 괜찮다 — 쉼은 재는 시간에 넣지 않는다', () => {
    const pattern = [...talk(2000), ...pause(1500), ...talk(2000), ...pause(1500), ...talk(2000)];
    expect(finishRead(read(pattern).state, true)).toEqual({
      kind: 'DONE',
      levelDb: -30,
      heardMs: 6000,
    });
  });

  it('일찍 누르면 조금이라도 들린 이상 BRIEF — 전혀 안 들렸을 때만 QUIET', () => {
    expect(finishRead(read(talk(3000)).state, true)).toEqual({ kind: 'SHORT', reason: 'BRIEF' });
    expect(finishRead(read(talk(500)).state, true)).toEqual({ kind: 'SHORT', reason: 'BRIEF' });
    expect(finishRead(read([]).state, true)).toEqual({ kind: 'SHORT', reason: 'QUIET' });
  });

  it('최대 시간까지 읽었는데 거의 안 잡혔으면 QUIET', () => {
    const quiet = read([...talk(500), ...pause(MAX_READ_MS)]);
    expect(finishRead(quiet.state, false)).toEqual({ kind: 'SHORT', reason: 'QUIET' });
  });

  /**
   * 사이사이가 비었는지는 보지 않습니다 — AI 문서의 조건은 "말한 구간만 · 5초 이상" 뿐입니다.
   * '말하는 중'은 실제 읽은 시간의 절반 남짓만 잡혀서, 쉬어 가며 읽은 사람을 다시 읽게 하면 안 됩니다.
   */
  it('쉬어 가며 읽어 말한 비율이 낮아도, 말한 시간을 채웠으면 잰다', () => {
    // 0.25초 소리 · 1.25초 쉼을 20번 — 말한 시간 5초, 말한 비율 약 17%
    const sparse = Array.from({ length: 20 }, () => [true, ...pause(1250)]).flat();
    expect(finishRead(read(sparse).state, true)).toEqual({
      kind: 'DONE',
      levelDb: -30,
      heardMs: 5000,
    });
  });
});

describe('누르지 않으면', () => {
  it('최대 시간이 지나야 끝난다 — 읽는 중에 소리로 멋대로 끝내지 않는다', () => {
    const reading = read([...talk(6000), ...pause(3000)]);
    expect(readTimedOut(reading.state, reading.now)).toBe(false);
    const long = read(pause(MAX_READ_MS + 250));
    expect(readTimedOut(long.state, long.now)).toBe(true);
  });
});
