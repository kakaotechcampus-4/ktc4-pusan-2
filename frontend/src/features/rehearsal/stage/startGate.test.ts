import { describe, expect, it } from 'vitest';
import { hasCamera, hasLiveAudio, startGate } from './startGate';

const base = {
  started: false,
  resumed: false,
  micLive: true,
  deviceError: null,
  connected: false,
  startFailure: null,
  slow: false,
} as const;

describe('무대 시계를 돌려도 되나', () => {
  it('마이크와 첫 ready 가 있어야 시작한다', () => {
    expect(startGate({ ...base, micLive: false })).toEqual({ kind: 'OPENING' });
    expect(startGate(base)).toEqual({ kind: 'CONNECTING', slow: false });
    expect(startGate({ ...base, connected: true })).toEqual({ kind: 'STARTED' });
  });

  it('장치를 못 열면 연결과 상관없이 시작하지 않는다', () => {
    expect(
      startGate({ ...base, micLive: false, deviceError: 'PERMISSION_DENIED', connected: true }),
    ).toEqual({ kind: 'NO_MIC', error: 'PERMISSION_DENIED' });
  });

  it('카메라를 못 열었어도 마이크가 있으면 시작한다 — 시선만 빠진다', () => {
    expect(startGate({ ...base, deviceError: 'IN_USE' })).toEqual({
      kind: 'CONNECTING',
      slow: false,
    });
    expect(startGate({ ...base, deviceError: 'PERMISSION_DENIED', connected: true })).toEqual({
      kind: 'STARTED',
    });
  });

  it('ready 전에 소켓이 포기하면 이유를 알린다 — 서버가 끝낸 Take 는 다시 시도해도 같다', () => {
    expect(startGate({ ...base, startFailure: 'ENDED' })).toEqual({
      kind: 'FAILED',
      reason: 'ENDED',
    });
    expect(startGate({ ...base, startFailure: 'GAVE_UP', slow: true })).toEqual({
      kind: 'FAILED',
      reason: 'GAVE_UP',
    });
  });

  it('오래 기다리면 slow 로 알린다', () => {
    expect(startGate({ ...base, slow: true })).toEqual({ kind: 'CONNECTING', slow: true });
  });

  it('새로고침으로 이어 가는 Take 는 ready 를 기다리지 않지만 마이크는 본다', () => {
    expect(startGate({ ...base, resumed: true })).toEqual({ kind: 'STARTED' });
    expect(startGate({ ...base, resumed: true, micLive: false, deviceError: 'IN_USE' })).toEqual({
      kind: 'NO_MIC',
      error: 'IN_USE',
    });
  });

  it('한 번 시작하면 마이크가 빠지거나 연결이 끊겨도 멈추지 않는다', () => {
    expect(
      startGate({
        ...base,
        started: true,
        micLive: false,
        deviceError: 'NOT_FOUND',
        startFailure: 'GAVE_UP',
      }),
    ).toEqual({ kind: 'STARTED' });
  });
});

describe('살아 있는 오디오 트랙', () => {
  const streamOf = (...states: MediaStreamTrackState[]) =>
    ({
      getAudioTracks: () => states.map((readyState) => ({ readyState })),
    }) as unknown as MediaStream;

  it('끝난 트랙만 있거나 트랙이 없으면 없는 것이다', () => {
    expect(hasLiveAudio(null)).toBe(false);
    expect(hasLiveAudio(streamOf())).toBe(false);
    expect(hasLiveAudio(streamOf('ended'))).toBe(false);
    expect(hasLiveAudio(streamOf('ended', 'live'))).toBe(true);
  });
});

describe('카메라를 열었나', () => {
  const streamOf = (...states: MediaStreamTrackState[]) =>
    ({
      getVideoTracks: () => states.map((readyState) => ({ readyState })),
    }) as unknown as MediaStream;

  it('마이크만 연 스트림은 카메라가 없다. 끝난 트랙은 연 것으로 본다', () => {
    expect(hasCamera(null)).toBe(false);
    expect(hasCamera(streamOf())).toBe(false);
    expect(hasCamera(streamOf('ended'))).toBe(true);
    expect(hasCamera(streamOf('live'))).toBe(true);
  });
});
