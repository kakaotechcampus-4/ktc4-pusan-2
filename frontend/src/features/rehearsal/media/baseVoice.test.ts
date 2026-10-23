import { describe, expect, it } from 'vitest';
import {
  EMPTY_RUN,
  MIN_VOICED_MS,
  QUIET_BELOW_MS,
  addRecord,
  baseLevelDb,
  END_SILENCE_MS,
  MAX_READ_MS,
  MORE_AFTER_SILENCE_MS,
  askMore,
  judgeRead,
  readStage,
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
    const from = now;
    now += 250;
    state = stepRead(state, rec(voiced ? level : null, voiced ? 250 : 0), from, now);
  }
  return { state, now };
}
const talk = (ms: number) => Array<boolean>(ms / 250).fill(true);
const pause = (ms: number) => Array<boolean>(ms / 250).fill(false);

describe('문장을 끝까지 읽었는가 — 내용은 보지 않고 소리로만', () => {
  it('말한 시간을 채워도 아직 말하고 있으면 끝내지 않는다 — 읽는 도중에 끝내지 않는다', () => {
    const { state, now } = read(talk(6000));
    expect(judgeRead(state, now, 1)).toEqual({ kind: 'READING' });
  });

  it('채운 뒤 말이 멈추면 끝내고, 들은 시간과 크기를 낸다', () => {
    const { state, now } = read([...talk(7000), ...pause(END_SILENCE_MS)]);
    expect(judgeRead(state, now, 1)).toEqual({ kind: 'DONE', levelDb: -30, heardMs: 7000 });
  });

  it('문장 사이 짧은 쉼은 괜찮다', () => {
    const pattern = [...talk(2000), ...pause(500), ...talk(2000), ...pause(500), ...talk(2000)];
    const { state, now } = read([...pattern, ...pause(END_SILENCE_MS)]);
    expect(judgeRead(state, now, 1).kind).toBe('DONE');
  });

  it('띄엄띄엄 들린 소리(기침 · 잡음)로 채웠으면 다시 읽게 한다', () => {
    // 0.25초 소리 · 1.25초 쉼을 20번 — 말한 시간 5초, 그 사이는 대부분 비어 있음 (비율 약 17%)
    const choppy = Array.from({ length: 20 }, () => [true, ...pause(1250)]).flat();
    const { state, now } = read([...choppy, ...pause(END_SILENCE_MS)]);
    expect(judgeRead(state, now, 1)).toEqual({ kind: 'SHORT', reason: 'CHOPPY' });
  });

  it('최대 시간이 지나면 끝낸다 — 채웠으면 결과, 못 채웠으면 이유', () => {
    const long = read(talk(MAX_READ_MS + 250));
    expect(judgeRead(long.state, long.now, 1).kind).toBe('DONE');
    const quiet = read([...talk(500), ...pause(MAX_READ_MS)]);
    expect(judgeRead(quiet.state, quiet.now, 1)).toEqual({ kind: 'SHORT', reason: 'QUIET' });
  });
});

describe('채우기 전에 멈추면 — 실패가 아니라 이어 읽기', () => {
  it('문장을 다 읽고 멈췄는데 모자라면, 다음 문장이 남아 있으면 이어 읽게 한다', () => {
    const { state, now } = read([...talk(4000), ...pause(MORE_AFTER_SILENCE_MS)]);
    expect(judgeRead(state, now, 3)).toEqual({ kind: 'MORE' });
  });

  it('이어 읽기를 청한 바로 뒤에는 다시 청하지 않는다', () => {
    const { state, now } = read([...talk(4000), ...pause(MORE_AFTER_SILENCE_MS)]);
    const asked = askMore(state, now);
    expect(asked.sentences).toBe(2);
    expect(judgeRead(asked, now + 250, 3)).toEqual({ kind: 'READING' });
  });

  it('이어 읽어 채우면 멈출 때 끝낸다 — 앞서 잰 시간도 그대로 더한다', () => {
    const first = read([...talk(4000), ...pause(MORE_AFTER_SILENCE_MS)]);
    let state = askMore(first.state, first.now);
    let now = first.now;
    for (const voiced of [...talk(2000), ...pause(END_SILENCE_MS)]) {
      const from = now;
      now += 250;
      state = stepRead(state, rec(voiced ? -30 : null, voiced ? 250 : 0), from, now);
    }
    expect(judgeRead(state, now, 3)).toEqual({ kind: 'DONE', levelDb: -30, heardMs: 6000 });
  });

  it('준비한 문장을 다 쓰고도 모자라면 그때 끝낸다 — 최대 시간까지 기다리지 않는다', () => {
    const { state, now } = read([...talk(4000), ...pause(MORE_AFTER_SILENCE_MS)]);
    expect(judgeRead(state, now, 1)).toEqual({ kind: 'SHORT', reason: 'BRIEF' });
  });
});
