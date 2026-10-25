import type { DeviceError } from '../media/useCameraStream';

/**
 * 무대 시계를 돌려도 되나. **React 를 모릅니다** — 값만 받아 값을 돌려줍니다.
 *
 * 마이크가 없거나 서버 연결이 `ready` 를 주지 않았으면 발표를 시작하지 않습니다 (#381).
 * 카메라는 보지 않습니다 — 못 열면 마이크만 열어 시작하고 시선만 뺍니다 (#445, `openDevices.ts`).
 * 그 연결이 서버에서 Take 를 시작하므로(`READY` → `RUNNING`), 연결 없이 시작한 Take 는
 * 1초 측정값도 전사도 남지 않아 결과를 만들 수 없습니다. 서버는 시작하지 않은 Take 를 만든 지
 * 10분 뒤 `ABANDONED` 로 끝내고, 그 Take 의 끝내기는 409 `TAKE_NOT_STARTED` 로 거절합니다.
 *
 * - **한 번 시작하면 끝까지 시작한 것입니다** (`started`). 발표 중에 마이크가 빠지거나 연결이 끊겨도
 *   시계를 멈추지 않습니다 — 그때는 말하기 분석만 빠지고 발표는 이어 갑니다 (CLAUDE.md 4번).
 * - **새로고침으로 이어 가는 Take** (`resumed`) 는 서버에서 이미 시작한 Take 라 `ready` 를 기다리지
 *   않습니다. 끊긴 지 오래면 서버가 `ABANDONED` 로 끝냈어도 끝내기 PUT 이 되살립니다. 마이크는 봅니다.
 */
export type StartGate =
  /** 카메라 · 마이크를 여는 중 */
  | { kind: 'OPENING' }
  /** 마이크를 못 열었거나 마이크 트랙이 없음 */
  | { kind: 'NO_MIC'; error: DeviceError | null }
  /** 서버 연결이 `ready` 를 주기를 기다리는 중. `slow` 면 오래 걸려 다시 시도를 띄웁니다 */
  | { kind: 'CONNECTING'; slow: boolean }
  /**
   * `ready` 전에 소켓이 포기했다. `ENDED` 는 서버가 이미 끝낸 Take(시작하지 않은 채 정리됨),
   * `GAVE_UP` 은 그 밖의 이유(인증 등)
   */
  | { kind: 'FAILED'; reason: 'ENDED' | 'GAVE_UP' }
  | { kind: 'STARTED' };

/** `ready` 를 이만큼 기다려도 안 오면 다시 시도 · 장치 점검으로 돌아가기를 보여 줍니다 */
export const CONNECT_SLOW_MS = 15_000;

export function startGate(state: {
  /** 이미 시작했다. 한 번 true 면 다른 값은 보지 않습니다 */
  started: boolean;
  /** 새로고침으로 이어 가는 Take (시계를 0 이 아닌 곳부터 돌린다) */
  resumed: boolean;
  /** 스트림에 살아 있는 오디오 트랙이 있다 */
  micLive: boolean;
  micError: DeviceError | null;
  /** 이 페이지의 소켓이 `ready` 를 받았다 */
  connected: boolean;
  startFailure: 'ENDED' | 'GAVE_UP' | null;
  /** `ready` 를 `CONNECT_SLOW_MS` 넘게 기다렸다 */
  slow: boolean;
}): StartGate {
  if (state.started) return { kind: 'STARTED' };
  if (!state.micLive) {
    return state.micError !== null
      ? { kind: 'NO_MIC', error: state.micError }
      : { kind: 'OPENING' };
  }
  if (state.resumed || state.connected) return { kind: 'STARTED' };
  if (state.startFailure !== null) return { kind: 'FAILED', reason: state.startFailure };
  return { kind: 'CONNECTING', slow: state.slow };
}

/** 스트림에 살아 있는 오디오 트랙이 있나. 장치를 열었어도 마이크 트랙이 빠져 있을 수 있습니다 */
export function hasLiveAudio(stream: MediaStream | null): boolean {
  return stream?.getAudioTracks().some((track) => track.readyState === 'live') ?? false;
}

/**
 * 카메라를 열었나. 끝난 트랙도 연 것으로 봅니다 — 발표 중에 빠진 카메라는 "끊김"으로 따로 다룹니다.
 * 마이크만 연 스트림이면 false 입니다 (`openDevices.ts` 3번)
 */
export function hasCamera(stream: MediaStream | null): boolean {
  return (stream?.getVideoTracks().length ?? 0) > 0;
}
