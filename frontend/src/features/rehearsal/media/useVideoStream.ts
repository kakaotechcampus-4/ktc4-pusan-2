import { useEffect, useRef, useState } from 'react';

/**
 * 스트림을 <video>에 물리고, **실제로 프레임이 흐르는지**를 알려줍니다.
 *
 * 스트림 객체가 생겼다고 영상이 나오는 건 아닙니다 — `playing`이 와야 나오는 것입니다.
 * 장치 점검에서 이 둘을 같은 것으로 보면, 카메라가 다른 앱에 물려 있는데도
 * 점검 통과로 넘어가 버립니다.
 *
 * live를 이펙트 안에서 직접 되돌리지 않습니다. 대신 "어느 스트림이 재생 중인가"를
 * 들고 있다가 렌더에서 비교합니다 — 스트림이 바뀌면 live는 저절로 false가 됩니다.
 */
export function useVideoStream(stream: MediaStream | null, tag: string) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playingFor, setPlayingFor] = useState<MediaStream | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    video.srcObject = stream;
    if (!stream) return;

    const onPlaying = () => setPlayingFor(stream);
    video.addEventListener('playing', onPlaying);
    video.play().catch((e: unknown) => {
      // 삼키면 '아무 일도 안 일어남'으로 보입니다 — 실제로 여기서 한참 헤맵니다
      console.error(`[${tag}] video.play failed`, e);
    });

    return () => video.removeEventListener('playing', onPlaying);
  }, [stream, tag]);

  return { videoRef, live: stream !== null && playingFor === stream };
}
