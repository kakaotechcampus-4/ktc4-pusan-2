import { useCallback, useEffect, useRef, useState } from 'react';

/** 장치 점검 화면에서 고른 카메라·마이크. 안 주면 브라우저 기본 장치입니다 */
export interface DeviceChoice {
  videoDeviceId?: string;
  audioDeviceId?: string;
}

export interface DeviceRequestOptions {
  /** 고른 마이크가 없을 때 기본 마이크를 허용합니다. 발표 화면의 기본 카메라 재시도에만 씁니다 */
  fallbackMic?: boolean;
}

export type DeviceError =
  | 'PERMISSION_DENIED' // 사용자가 거부
  | 'NOT_FOUND' // 카메라·마이크가 없음
  | 'IN_USE' // 다른 앱이 점유 (Zoom 등)
  | 'UNKNOWN';

/**
 * 지금 열려 있는 스트림 전부.
 *
 * 모듈 스코프에 두는 이유 — HMR dispose 는 컴포넌트 바깥에서 실행되므로
 * 훅 안의 streamRef 에 손이 닿지 않습니다. 놓아줄 대상을 여기 모아 둬야
 * dispose 가 실제로 트랙을 stop() 할 수 있습니다.
 */
const liveStreams = new Set<MediaStream>();

function release(s: MediaStream): void {
  s.getTracks().forEach((t) => t.stop());
  liveStreams.delete(s);
}

/**
 * 카메라·마이크 스트림.
 *
 * 화면 P4가 이 훅의 에러 3종을 각각 다른 문구로 보여줍니다 (명세 8-3).
 * "카메라를 사용할 수 없습니다" 하나로 뭉치면 사용자가 뭘 해야 할지 모릅니다.
 *
 * ── 개발 중 겪을 함정 ──
 * HMR이 돌 때 이전 스트림을 놓아주지 않으면 카메라가 물린 채로 남습니다.
 * 그러면 다음 저장부터 NotReadableError가 나고, 브라우저를 껐다 켜야 합니다.
 * 아래 import.meta.hot 블록이 그걸 막습니다. 지우지 마세요.
 */
export function useCameraStream() {
  const streamRef = useRef<MediaStream | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  // 장치 점검은 두 장치를 한 번에 요청한 오류를 그대로 씁니다.
  const [error, setError] = useState<DeviceError | null>(null);
  const [cameraError, setCameraError] = useState<DeviceError | null>(null);
  const [micError, setMicError] = useState<DeviceError | null>(null);

  // 요청마다 1씩 올립니다. 기다리는 사이 새 요청·언마운트가 오면 늦게 온 스트림은 바로 놓습니다
  const seqRef = useRef(0);
  // 언마운트는 번호 대신 이 값으로 봅니다. StrictMode 의 가짜 언마운트가 번호를 올리면 자동으로 연 스트림까지 버립니다
  const mountedRef = useRef(true);

  const stop = useCallback(() => {
    if (streamRef.current) release(streamRef.current);
    streamRef.current = null;
    setStream(null);
  }, []);

  const adopt = useCallback((s: MediaStream, seq: number) => {
    if (seq !== seqRef.current || !mountedRef.current) {
      s.getTracks().forEach((t) => t.stop());
      return null;
    }
    liveStreams.add(s);
    streamRef.current = s;
    setStream(s);
    return s;
  }, []);

  const request = useCallback(
    async (choice: DeviceChoice = {}, options: DeviceRequestOptions = {}) => {
      stop();
      const seq = ++seqRef.current;
      setError(null);
      setCameraError(null);
      setMicError(null);
      try {
        // deviceId 는 exact 로 겁니다 — ideal 로 주면 브라우저가 조용히 다른 장치를
        // 열어서, 사용자가 고른 마이크와 실제로 녹음되는 마이크가 달라집니다.
        const constraints: MediaStreamConstraints = {
          video: {
            width: 640,
            height: 480,
            frameRate: { ideal: 15 },
            ...(choice.videoDeviceId ? { deviceId: { exact: choice.videoDeviceId } } : {}),
          },
          audio: audioConstraints(choice.audioDeviceId),
        };
        const s = await navigator.mediaDevices.getUserMedia(constraints).catch((e: unknown) => {
          if (!options.fallbackMic || !choice.audioDeviceId || toDeviceError(e) !== 'NOT_FOUND') {
            throw e;
          }
          // 기본 카메라와 고른 마이크를 함께 못 찾았을 때만 기본 마이크로 다시 엽니다.
          return navigator.mediaDevices.getUserMedia({ ...constraints, audio: audioConstraints() });
        });
        return adopt(s, seq);
      } catch (e) {
        setError(toDeviceError(e));
        // 마이크 오류인지는 마이크만 다시 열어 확인합니다.
        setCameraError(toDeviceError(e));
        return null;
      }
    },
    [stop, adopt],
  );

  /**
   * 마이크만 엽니다. 발표 화면이 카메라를 못 열었을 때 씁니다 — 카메라는 발표 시작 조건이 아닙니다.
   * 고른 마이크가 빠졌을 때만 기본 마이크로 넘어갑니다.
   * 카메라 오류는 유지하고, 마이크를 못 연 이유는 `micError` 에 따로 둡니다.
   */
  const requestMic = useCallback(
    async (audioDeviceId?: string) => {
      stop();
      const seq = ++seqRef.current;
      setMicError(null);
      try {
        const s = await navigator.mediaDevices
          .getUserMedia({ audio: audioConstraints(audioDeviceId) })
          .catch((e: unknown) => {
            if (!audioDeviceId || toDeviceError(e) !== 'NOT_FOUND') throw e;
            return navigator.mediaDevices.getUserMedia({ audio: audioConstraints() });
          });
        return adopt(s, seq);
      } catch (e) {
        setMicError(toDeviceError(e));
        return null;
      }
    },
    [stop, adopt],
  );

  // 언마운트 시 반드시 놓아줍니다. 안 그러면 카메라 불이 안 꺼집니다.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stop();
    };
  }, [stop]);

  // 트랙 종료도 봅니다. devicechange 없이 마이크가 끝나도 시작 덮개를 갱신해야 합니다.
  useEffect(() => {
    if (!stream) return;
    const onChange = () => {
      const video = stream.getVideoTracks();
      if (video.length > 0 && !video.some((t) => t.readyState === 'live')) {
        setError('NOT_FOUND');
        setCameraError('NOT_FOUND');
      }
      const audio = stream.getAudioTracks();
      if (!audio.some((t) => t.readyState === 'live')) setMicError('NOT_FOUND');
    };
    const tracks = stream.getTracks();
    onChange();
    tracks.forEach((track) => track.addEventListener('ended', onChange));
    navigator.mediaDevices?.addEventListener('devicechange', onChange);
    return () => {
      tracks.forEach((track) => track.removeEventListener('ended', onChange));
      navigator.mediaDevices?.removeEventListener('devicechange', onChange);
    };
  }, [stream]);

  return { stream, error, cameraError, micError, request, requestMic, stop };
}

function audioConstraints(deviceId?: string): MediaTrackConstraints {
  return {
    echoCancellation: true,
    noiseSuppression: true,
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  };
}

function toDeviceError(e: unknown): DeviceError {
  const name = (e as { name?: string })?.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'PERMISSION_DENIED';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'NOT_FOUND';
  if (name === 'NotReadableError' || name === 'AbortError') return 'IN_USE';
  return 'UNKNOWN';
}

export const DEVICE_ERROR_MESSAGE: Record<DeviceError, string> = {
  PERMISSION_DENIED: '카메라와 마이크 사용을 허용해 주세요. 주소창 왼쪽 자물쇠에서 바꿀 수 있어요.',
  NOT_FOUND: '연결된 카메라나 마이크를 찾지 못했어요.',
  IN_USE: '다른 앱이 카메라를 쓰고 있어요. Zoom이나 화상회의를 닫고 다시 시도해 주세요.',
  UNKNOWN: '카메라를 시작하지 못했어요. 페이지를 새로고침해 주세요.',
};

/** 마이크를 못 열었을 때 (발표 화면의 시작 덮개). 위 문구는 카메라를 앞세워서 따로 둡니다 */
export const MIC_ERROR_MESSAGE: Record<DeviceError, string> = {
  PERMISSION_DENIED: '마이크 사용을 허용해 주세요. 주소창 왼쪽 자물쇠에서 바꿀 수 있어요.',
  NOT_FOUND: '연결된 마이크를 찾지 못했어요.',
  IN_USE: '다른 앱이 마이크를 쓰고 있어요. Zoom이나 화상회의를 닫고 다시 시도해 주세요.',
  UNKNOWN: '마이크를 시작하지 못했어요.',
};

// HMR에서 스트림을 확실히 놓아줍니다 — 개발 중에만 동작합니다. 지우지 마세요.
//
// 이 블록이 없으면 저장할 때마다 이전 스트림이 카메라를 붙잡고 남습니다.
// 그러면 다음 저장부터 NotReadableError(IN_USE)가 나고, 브라우저를 껐다 켜야 합니다.
// fps 측정 중이라면 그 측정값도 오염됩니다.
//
// ★ getUserMedia 를 다시 부르는 방식으로는 안 됩니다. 그건 새 스트림을 *요청*하는
//   것이어서 이미 열린 트랙을 놓아주지 못하고, {video:false, audio:false} 는
//   명세상 TypeError 로 거부됩니다 — 조용히 아무 일도 일어나지 않습니다.
//   열려 있는 트랙을 직접 stop() 하는 것만이 카메라 표시등을 끕니다.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    for (const s of liveStreams) {
      s.getTracks().forEach((t) => t.stop());
    }
    liveStreams.clear();
  });
}
