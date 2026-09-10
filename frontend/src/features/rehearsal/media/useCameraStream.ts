import { useCallback, useEffect, useRef, useState } from 'react';

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
  const [error, setError] = useState<DeviceError | null>(null);

  const stop = useCallback(() => {
    if (streamRef.current) release(streamRef.current);
    streamRef.current = null;
    setStream(null);
  }, []);

  const request = useCallback(async () => {
    stop();
    setError(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, frameRate: { ideal: 15 } },
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      liveStreams.add(s);
      streamRef.current = s;
      setStream(s);
      return s;
    } catch (e) {
      setError(toDeviceError(e));
      return null;
    }
  }, [stop]);

  // 언마운트 시 반드시 놓아줍니다. 안 그러면 카메라 불이 안 꺼집니다.
  useEffect(() => stop, [stop]);

  // 발표 중 장치가 빠지는 경우 (명세 8-3 · 카메라만 소실)
  useEffect(() => {
    const onChange = () => {
      const alive = streamRef.current?.getVideoTracks().some((t) => t.readyState === 'live');
      if (streamRef.current && !alive) setError('NOT_FOUND');
    };
    navigator.mediaDevices?.addEventListener('devicechange', onChange);
    return () => navigator.mediaDevices?.removeEventListener('devicechange', onChange);
  }, []);

  return { stream, error, request, stop };
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
