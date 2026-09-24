import { useCallback, useEffect, useRef, useState } from 'react';
import {
  STT_ERROR_MESSAGE,
  STT_STATE_NOTE,
  type SttState,
  type TranscriptMessage,
} from '@/features/rehearsal/lib/sttProtocol';
import { SttSocket } from '@/features/rehearsal/lib/sttSocket';
import {
  PCM_CAPTURE_ERROR_MESSAGE,
  startPcmCapture,
  type PcmCapture,
} from '@/features/rehearsal/media/pcmCapture';
import type { Ms } from '@/types/api';

/**
 * 2단(서버) 코치의 입력선 — 마이크를 16kHz PCM 으로 서버에 흘리고 전사를 받습니다.
 *
 * ── 화면에 무엇을 어떻게 내보내나 ──────────────────────────────────
 *
 * | 값 | 어디에 | 왜 |
 * | --- | --- | --- |
 * | 전사(중간·확정) | **DOM 직접** (`transcriptRef`) | 100~300ms 마다 옵니다. 상태로 올리면 시선 fps 가 깎입니다 |
 * | `stt_state` | React 상태 | 전이가 드뭅니다 (connecting → ok → …). 문구가 바뀌어야 합니다 |
 * | 프레임·유실 누적 | ref | 계기판용입니다. 발표자에게는 안 보입니다 |
 *
 * ── 끊겨도 발표는 계속됩니다 ───────────────────────────────────────
 *
 * 이 훅이 실패하는 모든 경로(마이크 없음·16kHz 거절·인증 만료·재연결 소진)는
 * **문구 한 줄**로 끝납니다. 녹음과 시선은 그대로 돌고, 원본은 브라우저에
 * 남아 있습니다 (CLAUDE.md 4번).
 */
export function useSttStream({
  takeId,
  stream,
  enabled,
  elapsedMs,
}: {
  takeId: string;
  stream: MediaStream | null;
  /** 발표가 도는 동안만 true. 종료 뒤에는 붙지 않습니다 */
  enabled: boolean;
  /** 캡처를 시작한 시점의 Take 경과 시간 — offset_ms 의 기준점입니다 */
  elapsedMs: () => Ms;
}) {
  /** 전사 한 줄이 들어갈 자리. 이 훅이 textContent 로 직접 씁니다 */
  const transcriptRef = useRef<HTMLSpanElement>(null);
  const socketRef = useRef<SttSocket | null>(null);
  const captureRef = useRef<PcmCapture | null>(null);

  /** 확정된 마지막 문장. 중간 결과가 비면 이것을 보여 줍니다 */
  const lastFinalRef = useRef('');
  const statsRef = useRef({ frames: 0, lostMs: 0 });

  const [sttState, setSttState] = useState<SttState | null>(null);
  const [errorNote, setErrorNote] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !stream || takeId === '') return;

    let cancelled = false;

    /**
     * 소켓이 포기했다. **캡처 준비가 그보다 늦게 끝날 수 있어서** 따로 둡니다 —
     * 인증이 먼저 실패하면 `onGiveUp` 시점에는 아직 `captureRef` 가 비어 있고,
     * 그 뒤에 준비가 끝난 캡처를 그대로 받아 두면 보낼 곳도 없는 워클릿이
     * 남은 발표 내내 돕니다.
     */
    let abandoned = false;

    const paint = (message: TranscriptMessage) => {
      if (message.is_final) lastFinalRef.current = message.text;
      const text = message.is_final ? message.text : message.text || lastFinalRef.current;
      if (transcriptRef.current) transcriptRef.current.textContent = text;
    };

    const socket = new SttSocket({
      takeId,
      onTranscript: paint,
      onState: (state) => {
        if (cancelled) return;
        setSttState(state);
        // 다시 붙었으면 지난 경고는 치웁니다 — 남아 있으면 계속 고장 난 것처럼 보입니다
        if (state === 'ok') setErrorNote(null);
      },
      onStatus: (message) => {
        statsRef.current = { frames: message.frames, lostMs: message.lost_ms };
      },
      onError: (code) => {
        if (!cancelled) setErrorNote(STT_ERROR_MESSAGE[code]);
      },
      onGiveUp: (reason) => {
        // 보낼 곳이 없어졌습니다. 마이크 캡처를 계속 돌리면 남은 발표 내내
        // 오디오 스레드와 워클릿이 헛돕니다 — 같은 화면에서 시선 워커가 돌고 있습니다
        abandoned = true;
        captureRef.current?.stop().catch(() => undefined);
        captureRef.current = null;

        if (cancelled || reason === 'FATAL') return;

        // FATAL 은 위 onError 가 이미 이유를 말했습니다. 나머지는 여기서 말합니다
        setErrorNote(
          reason === 'NO_TOKEN'
            ? STT_ERROR_MESSAGE.UNAUTHORIZED
            : '말하기 분석 연결이 끊겼어요. 발표는 계속하세요.',
        );
      },
    });
    socketRef.current = socket;
    socket.start();

    startPcmCapture(stream, {
      elapsedMs,
      onFrame: ({ pcm, offsetMs }) => socket.sendFrame(pcm, offsetMs),
    })
      .then(({ capture, error }) => {
        // abandoned — 준비가 끝나기 전에 소켓이 포기한 경우입니다
        if (cancelled || abandoned || error) {
          capture?.stop().catch(() => undefined);
          // 오디오가 없으면 소켓을 붙잡고 있을 이유가 없습니다
          if (error) {
            socket.dispose();
            if (!cancelled) setErrorNote(PCM_CAPTURE_ERROR_MESSAGE[error]);
          }
          return;
        }
        captureRef.current = capture;
      })
      .catch(() => {
        // 여기서 삼키면 화면에는 아무 표시 없이 말하기 분석만 사라집니다
        socket.dispose();
        if (!cancelled) setErrorNote(PCM_CAPTURE_ERROR_MESSAGE.WORKLET_FAILED);
      });

    return () => {
      cancelled = true;
      captureRef.current?.stop().catch(() => undefined);
      captureRef.current = null;
      socketRef.current = null;
      socket.dispose();
    };
  }, [enabled, stream, takeId, elapsedMs]);

  /**
   * 종료 CTA 가 **화면을 정리하기 전에** 부릅니다.
   *
   * 마이크를 먼저 끊고(더 보낼 것이 없다) `stop` 을 보낸 뒤 서버가 `closed` 를
   * 줄 때까지 기다립니다. 여기서 안 기다리면 마지막 문장의 전사가 사라집니다.
   * 서버가 늦으면 15초에서 끊습니다 — 종료 화면을 붙잡아 두지 않습니다.
   */
  const stop = useCallback(async () => {
    const capture = captureRef.current;
    captureRef.current = null;
    await capture?.stop().catch(() => undefined);

    const socket = socketRef.current;
    socketRef.current = null;
    await socket?.stop();
  }, []);

  return {
    transcriptRef,
    sttState,
    /** 발밑에 뜨는 한 줄. 에러가 있으면 에러가 이깁니다 */
    note: errorNote ?? (sttState ? STT_STATE_NOTE[sttState] : null),
    /** 눈에 띄게 할 것인가. 정상으로 도는 동안에는 조용히 둡니다 */
    alert: errorNote !== null || sttState === 'reconnecting' || sttState === 'degraded',
    statsRef,
    stop,
  };
}
