import { describe, expect, it } from 'vitest';
import { MAX_GAP_MS, createVoiceRecorder, type VoiceRecorder } from './voiceRecord';

/** rAF 한 번 간격(60fps)에 가깝게, 나눠 떨어지는 값 */
const FRAME_MS = 20;

/**
 * `[fromMs, toMs)` 동안 프레임마다 같은 값을 넣습니다.
 * 측정은 프레임 끝 시각에 들어오므로 `fromMs + FRAME_MS` 부터 `toMs` 까지 넣습니다.
 */
function feed(
  r: VoiceRecorder,
  fromMs: number,
  toMs: number,
  { levelDb = -30, speaking = true, live = true } = {},
) {
  for (let t = fromMs + FRAME_MS; t <= toMs; t += FRAME_MS)
    r.push({ tMs: t, levelDb, speaking, live });
}

/** 기록을 시작하는 첫 측정. 직전 값이 없어 공백으로 세지 않도록 구간 밖에 하나 둡니다 */
function start(r: VoiceRecorder, atMs: number) {
  r.push({ tMs: atMs, levelDb: -80, speaking: false, live: true });
}

describe('음량 1초 기록', () => {
  it('계속 말하면 그 1초가 다 말한 시간이고, 크기는 그 레벨이다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1000, { levelDb: -30 });
    expect(r.record(0, 1000)).toEqual({
      t_ms: 0,
      duration_ms: 1000,
      level_db: -30,
      voiced_ms: 1000,
      silence_ms: 0,
      audio_live: true,
    });
  });

  it('크기는 말한 시간만으로 낸다 — 침묵이 섞여도 작아지지 않는다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 400, { levelDb: -30 });
    feed(r, 400, 1000, { levelDb: -80, speaking: false });
    const rec = r.record(0, 1000);
    expect(rec.voiced_ms).toBe(400);
    expect(rec.level_db).toBe(-30);
    // 끝의 600ms 가 이어진 침묵입니다
    expect(rec.silence_ms).toBe(600);
  });

  it('크기는 에너지로 평균한다 — dB 를 평균하지 않는다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 500, { levelDb: -20 });
    feed(r, 500, 1000, { levelDb: -40 });
    // dB 평균이면 -30. 에너지 평균은 큰 쪽에 가깝습니다: 10·log10((0.01 + 0.0001) / 2) ≈ -22.97
    expect(r.record(0, 1000).level_db).toBeCloseTo(-23.0, 1);
  });

  it('말하지 않은 1초는 크기가 없다(null) — 0 이 아니다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1000, { speaking: false, levelDb: -70 });
    expect(r.record(0, 1000)).toMatchObject({ level_db: null, voiced_ms: 0, silence_ms: 1000 });
  });

  it('침묵은 앞 1초에서 이어 센다 — 1초를 넘을 수 있다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 700, { levelDb: -30 });
    feed(r, 700, 3000, { speaking: false, levelDb: -70 });
    expect(r.record(1000, 2000).silence_ms).toBe(1300);
    expect(r.record(2000, 3000).silence_ms).toBe(2300);
  });

  it('시선 격자처럼 1000 의 배수가 아닌 구간도 그대로 받는다', () => {
    const r = createVoiceRecorder();
    start(r, 59_380);
    feed(r, 59_380, 61_400, { levelDb: -30 });
    expect(r.record(59_390, 60_390)).toMatchObject({
      t_ms: 59_390,
      duration_ms: 1000,
      voiced_ms: 1000,
    });
  });

  it('마지막 짧은 조각도 그 길이로 낸다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1400, { levelDb: -30 });
    expect(r.record(1000, 1400)).toMatchObject({ duration_ms: 400, voiced_ms: 400 });
  });
});

describe('오디오가 흐르지 않은 시간', () => {
  it(`측정이 ${MAX_GAP_MS}ms 넘게 비면 그 1초는 audio_live false 이고, 공백은 말한 시간으로 세지 않는다`, () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 300, { levelDb: -30 });
    // 탭이 가려져 rAF 가 멈췄다가 0.6초 뒤에 돌아왔습니다
    feed(r, 900, 1000, { levelDb: -30 });
    const rec = r.record(0, 1000);
    expect(rec.audio_live).toBe(false);
    // 앞 300ms + 돌아온 뒤 80ms. 돌아온 첫 측정(920ms)은 공백을 덮는 칸이라 세지 않습니다
    expect(rec.voiced_ms).toBe(380);
  });

  it('공백 뒤의 침묵은 공백 앞에서 이어 세지 않는다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 500, { speaking: false, levelDb: -70 });
    feed(r, 1500, 2000, { speaking: false, levelDb: -70 });
    // 공백(500→1520) 뒤 첫 측정은 끊긴 칸이라 0, 그 뒤부터 다시 셉니다
    expect(r.record(1000, 2000).silence_ms).toBe(480);
  });

  it('오디오가 멈춰 있었으면(suspended · 트랙 끊김) audio_live false 이고 말한 시간도 없다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1000, { live: false, levelDb: -30 });
    expect(r.record(0, 1000)).toMatchObject({ audio_live: false, voiced_ms: 0, level_db: null });
  });

  it('측정이 하나도 없는 구간은 audio_live false 다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1000);
    expect(r.record(5000, 6000)).toMatchObject({ audio_live: false, voiced_ms: 0, level_db: null });
  });

  it('구간 끝까지 측정이 오지 않았으면 그 끝은 공백이다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 600);
    expect(r.record(0, 1000).audio_live).toBe(false);
  });
});

describe('시계', () => {
  it('시계가 뒤로 가면 앞 기록을 버린다 — 다른 시간축의 값이 섞이지 않는다', () => {
    const r = createVoiceRecorder();
    start(r, 10_000);
    feed(r, 10_000, 11_000, { levelDb: -30 });
    start(r, 0);
    feed(r, 0, 1000, { speaking: false, levelDb: -70 });
    expect(r.record(10_000, 11_000).audio_live).toBe(false);
    expect(r.record(0, 1000)).toMatchObject({ voiced_ms: 0, audio_live: true });
  });

  it('reset 하면 비운다', () => {
    const r = createVoiceRecorder();
    start(r, 0);
    feed(r, 0, 1000);
    r.reset();
    expect(r.record(0, 1000).audio_live).toBe(false);
  });
});
