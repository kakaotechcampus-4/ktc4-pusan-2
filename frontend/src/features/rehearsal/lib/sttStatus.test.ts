import { describe, expect, it } from 'vitest';
import { STT_STATE_MESSAGE, hasLostAudio, type SttState, type SttStatus } from './sttStatus';

const status = (patch: Partial<SttStatus> = {}): SttStatus => ({
  state: 'ok',
  sttSessionNo: 1,
  frames: 100,
  droppedFrames: 0,
  silenceMs: 0,
  lostMs: 0,
  ...patch,
});

describe('STT 상태 문구', () => {
  it('BE 가 보내는 다섯 상태를 모두 안다', () => {
    const states: SttState[] = ['connecting', 'ok', 'reconnecting', 'degraded', 'closed'];
    for (const s of states) expect(STT_STATE_MESSAGE).toHaveProperty(s);
    expect(Object.keys(STT_STATE_MESSAGE)).toHaveLength(states.length);
  });

  it('정상일 때는 아무것도 띄우지 않는다 — 발표 중 화면을 차지하지 않는다', () => {
    expect(STT_STATE_MESSAGE.ok).toBeNull();
    expect(STT_STATE_MESSAGE.closed).toBeNull();
  });

  /**
   * ★ degraded 는 오류가 아닙니다. BE 는 연결을 끊지 않고 재시도하며,
   *   1단 코치(브라우저 규칙)는 계속 돕니다. 문구가 발표를 멈추라고 읽히면 안 됩니다.
   */
  it('degraded 는 발표가 계속된다고 말한다', () => {
    expect(STT_STATE_MESSAGE.degraded).toContain('계속');
  });

  it('문제 있는 상태에는 문구가 있다', () => {
    expect(STT_STATE_MESSAGE.connecting).not.toBeNull();
    expect(STT_STATE_MESSAGE.reconnecting).not.toBeNull();
    expect(STT_STATE_MESSAGE.degraded).not.toBeNull();
  });
});

describe('hasLostAudio', () => {
  it('아직 상태를 못 받았으면 false', () => {
    expect(hasLostAudio(null)).toBe(false);
  });

  it('lostMs 가 0 이면 false — dropped 가 있어도 STT 에는 닿았다', () => {
    expect(hasLostAudio(status({ droppedFrames: 7 }))).toBe(false);
  });

  it('lostMs 가 있으면 true', () => {
    expect(hasLostAudio(status({ lostMs: 1200 }))).toBe(true);
  });
});
