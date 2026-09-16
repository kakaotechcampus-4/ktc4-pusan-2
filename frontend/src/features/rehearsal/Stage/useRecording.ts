import { useEffect, useRef, useState } from 'react';
import {
  RECORDER_ERROR_MESSAGE,
  startRecording,
  type RecorderError,
  type RecordingHandle,
} from '@/features/rehearsal/media/recorder';
import { audioBytes } from '@/features/rehearsal/lib/db';

/** 크기 표시 갱신 주기. 조각이 5초마다 들어오니 그보다 자주 볼 이유가 없습니다 */
const SIZE_TICK_MS = 5_000;

/**
 * 발표 녹음. **이것이 원본입니다** (CLAUDE.md 4번).
 *
 * 서버로 실시간으로 흘리는 PCM이 끊겨도 여기 남은 것으로 발표 뒤에 다시 돌립니다.
 * 그래서 녹음이 실패하면 발표를 막는 게 아니라 **알리고 계속 갑니다** —
 * 시선과 시간은 그대로 기록되고, 말하기 분석만 빠집니다.
 *
 * 5초 조각으로 받아 즉시 IndexedDB로 넘깁니다. 배열에 모으면 10분치 수십 MB가
 * 힙에 남고, 그 상태에서 시선 워커가 돌면 GC 압력이 fps로 나타납니다.
 */
export function useRecording({
  stream,
  clientSessionId,
  enabled,
}: {
  stream: MediaStream | null;
  clientSessionId: string | null;
  enabled: boolean;
}) {
  const handleRef = useRef<RecordingHandle | null>(null);
  const sizeRef = useRef<HTMLSpanElement>(null);

  /**
   * "어느 스트림에서, 어떻게 됐나". 스트림과 같이 들고 있어야
   * 장치가 바뀌었을 때 이전 녹음의 상태가 남아 있지 않습니다.
   */
  const [result, setResult] = useState<{ stream: MediaStream; error: RecorderError | null } | null>(
    null,
  );

  useEffect(() => {
    if (!enabled || !stream || !clientSessionId) return;

    let cancelled = false;

    // 시선 판정의 tMs와 같은 기준이어야 두 기록을 나중에 겹쳐 볼 수 있습니다.
    // 무대 시계도 performance.now() 기준이라 둘의 차이는 시작 호출 사이의 몇 ms뿐입니다.
    const t0 = performance.now();
    const { handle, error } = startRecording(stream, clientSessionId, t0, (e) => {
      if (!cancelled) setResult({ stream, error: e });
    });
    handleRef.current = handle;

    // 결과 알림은 마이크로태스크로 미룹니다. 이펙트 본문에서 상태를 바꾸면
    // 같은 커밋에서 렌더가 한 번 더 돌고, 그 렌더가 발표 시작 순간과 겹칩니다
    void Promise.resolve().then(() => {
      if (!cancelled) setResult({ stream, error });
    });

    const paintSize = async () => {
      const bytes = await audioBytes(clientSessionId);
      if (sizeRef.current) sizeRef.current.textContent = `${(bytes / 1024 / 1024).toFixed(1)}MB`;
    };
    void paintSize();
    const id = window.setInterval(() => void paintSize(), SIZE_TICK_MS);

    return () => {
      cancelled = true;
      window.clearInterval(id);
      // 마지막 조각까지 받고 멈춥니다 — 기다리지 않으면 마지막 5초가 잘립니다
      const h = handleRef.current;
      handleRef.current = null;
      void h?.stop();
    };
  }, [enabled, stream, clientSessionId]);

  /** 종료 CTA가 먼저 부릅니다. 페이로드를 만들기 전에 마지막 조각이 들어와야 합니다 */
  const stop = async () => {
    const h = handleRef.current;
    handleRef.current = null;
    await h?.stop();
  };

  const error = result?.stream === stream ? result.error : null;
  const recording = enabled && stream !== null && result?.stream === stream && error === null;

  return {
    sizeRef,
    recording,
    error,
    errorMessage: error ? RECORDER_ERROR_MESSAGE[error] : null,
    stop,
  };
}
