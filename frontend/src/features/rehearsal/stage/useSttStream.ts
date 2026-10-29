import { useCallback, useEffect, useRef, useState } from 'react';
import {
  STT_ABANDONED_MESSAGE,
  STT_ERROR_MESSAGE,
  STT_STATE_NOTE,
  type CoachMessage,
  type MetricsMessage,
  type SlideMessage,
  type SttState,
} from '../lib/sttProtocol';
import { SttSocket, type HandedMetrics } from '../lib/sttSocket';
import { PCM_CAPTURE_ERROR_MESSAGE, startPcmCapture, type PcmCapture } from '../media/pcmCapture';
import type { Ms } from '@/types/api';

/**
 * 리허설의 서버 연결 — 마이크를 16kHz PCM 으로 흘리고, 1초 기록(`metrics`) · 장 넘김(`slide`)을 보내고,
 * AI 코치의 `coach` 를 받습니다. 연결은 WebSocket 하나입니다 (CLAUDE.md 4번).
 *
 * ── 화면에 무엇을 어떻게 내보내나 ──────────────────────────────────
 *
 * | 값 | 어디에 | 왜 |
 * | --- | --- | --- |
 * | 전사(중간·확정) | **띄우지 않음** | 자기 말이 글로 따라붙으면 그걸 읽게 됩니다. 전사는 서버에 쌓여 리포트에서 씁니다 |
 * | `stt_state` | React 상태 | 전이가 드뭅니다 (connecting → ok → …). 문구가 바뀌어야 합니다 |
 * | 프레임·유실 누적 | ref | 계기판용입니다. 발표자에게는 안 보입니다 |
 *
 * ── 시작은 첫 `ready` 뒤에 ─────────────────────────────────────────
 *
 * 무대는 이 소켓이 첫 `ready` 를 받은 뒤에(`connected`) 시계를 돌립니다. 그 연결이 서버에서
 * Take 를 시작하므로(`RUNNING`), 연결 없이 시작한 Take 는 기록이 남지 않습니다. `ready` 전에
 * 소켓이 포기하면 `startFailure` 로 알리고 무대는 시작하지 않습니다.
 *
 * ── 시작한 뒤에는 끊겨도 발표는 계속됩니다 ─────────────────────────
 *
 * 이 훅이 실패하는 모든 경로(16kHz 거절·인증 만료)는
 * **문구 한 줄**로 끝납니다. 시선 기록 · 시계 · 슬라이드는 그대로 돕니다. 실시간 코치는 이 소켓으로
 * 오므로 소켓이 끝나면 함께 멈춥니다 — 마이크 캡처만 실패하면 소켓은 그대로라 코치도 계속 옵니다.
 * 네트워크 끊김은 실패로 치지 않습니다 — 소켓이 발표가 끝날 때까지 다시 붙어 보고,
 * 그동안 마이크 캡처도 계속 돕니다. 붙는 순간 말하기 분석이 이어집니다.
 * 끊긴 동안의 음성은 어디에도 남지 않아 그 구간의 말하기 분석은 빠집니다 (CLAUDE.md 4번).
 *
 * 마이크 캡처가 실패해도 소켓은 닫지 않습니다. 이 소켓이 1초 측정값(`metrics`)의 유일한 길이라,
 * 닫으면 카메라 · 시선이 멀쩡해도 그 Take 의 원자료가 서버에 하나도 가지 않습니다.
 * 오디오 없이 붙어 있어도 BE 가 Deepgram 에 KeepAlive 를 보내고, 말 없음 자동 종료는
 * 전사된 오디오로만 재므로 걸리지 않습니다.
 */
export function useSttStream({
  takeId,
  stream,
  enabled,
  capture,
  attempt = 0,
  elapsedMs,
  onAutoEnd,
  restoredMetrics,
  onClosed,
  onCoach,
  onConnection,
}: {
  takeId: string;
  stream: MediaStream | null;
  /** 무대가 열린 동안 true. 종료 뒤에는 붙지 않습니다. 시계보다 먼저 켜져 첫 `ready` 를 받아 옵니다 */
  enabled: boolean;
  /**
   * 마이크 PCM 을 보내기 시작해도 되나. **무대 시계가 돈 뒤에** true 여야 합니다 — 캡처는 시작할 때
   * 시계를 읽어 `offset_ms` 의 기준점을 잡아서, 시계가 멈춘 동안 걸면 기다린 시간만큼 전사가 어긋납니다
   */
  capture: boolean;
  /** 바꾸면 소켓을 새로 만듭니다 (첫 연결이 안 될 때 '다시 시도') */
  attempt?: number;
  /** 캡처를 시작한 시점의 Take 경과 시간 — offset_ms 의 기준점입니다 */
  elapsedMs: () => Ms;
  /** 서버가 Take 를 자동으로 끝냈습니다 (말 없음 · 최대 시간). 무대가 종료 흐름을 탑니다 */
  onAutoEnd?: () => void;
  /**
   * 새로고침 전 페이지가 못 보낸 `metrics` 를 꺼냅니다 (`takeMetrics`). 소켓을 만들 때 한 번 부르고,
   * 붙자마자 그것부터 보냅니다
   */
  restoredMetrics?: () => HandedMetrics[];
  /**
   * 창을 닫아 끝난 Take 입니다 (`TAKE_ENDED` · `CLOSED`). 닫은 탭을 늦게 복원했거나 다른 창이
   * 실패로 끝냈습니다. 무대는 이어 가지 않고 실패 화면으로 갑니다.
   * `message` 는 BE 가 보낸 문구입니다 — 다른 곳에서 새 Take 를 시작해 끝냈는지 이것으로 압니다
   */
  onClosed?: (message: string) => void;
  /** AI 코치의 한마디 · 상태 표시. 서버가 `COACHING` 모드에서만 보냅니다 (`useLiveCoach`) */
  onCoach?: (message: CoachMessage) => void;
  /** 서버와 이어졌다 · 끊겼다 (`SttSocket` `onConnection`). 코치 상태 표시가 낡았는지 압니다 */
  onConnection?: (connected: boolean) => void;
}) {
  const socketRef = useRef<SttSocket | null>(null);
  /**
   * 이 페이지에서 보낸 장 변경 전부. 소켓이 없을 때(카메라 · 마이크가 열리기 전) 보낸 것도 여기 남고,
   * 소켓이 `ready` 를 받을 때마다 전부 다시 보냅니다 (`SttSocket` `slideHistory`)
   */
  const slidesRef = useRef<SlideMessage[]>([]);
  // 다른 Take 의 장을 보내지 않습니다. 소켓을 만드는 이펙트보다 먼저 둡니다
  useEffect(() => {
    slidesRef.current = [];
  }, [takeId]);
  const captureRef = useRef<PcmCapture | null>(null);

  const statsRef = useRef({ frames: 0, lostMs: 0 });

  const [sttState, setSttState] = useState<SttState | null>(null);
  const [errorNote, setErrorNote] = useState<string | null>(null);
  /**
   * 마이크 캡처 실패. 소켓이 다시 붙어도(`ok`) 마이크는 그대로 고장이라 `errorNote` 와 따로 둡니다 —
   * 같이 두면 재연결 한 번에 지워져 말하기 분석이 도는 것처럼 보입니다
   */
  const [captureNote, setCaptureNote] = useState<string | null>(null);
  /**
   * 지금 소켓을 가리키는 값. 이 값이 바뀌면 소켓을 새로 만듭니다 (다시 시도 · 장치를 다시 엶).
   * 아래 두 상태는 어느 소켓의 결과인지 이 값으로 적어 두어, 새 소켓에서는 저절로 비어 보입니다
   */
  const socketKey = enabled && stream && takeId !== '' ? `${takeId}:${attempt}:${stream.id}` : null;
  /** `ready` 를 받은 소켓. 무대는 지금 소켓이 받은 뒤에 시계를 돌립니다 */
  const [readyKey, setReadyKey] = useState<string | null>(null);
  /**
   * 첫 `ready` 전에 소켓이 포기했다. `ENDED` 는 서버가 이미 끝낸 Take(시작하지 않은 채 정리됨 등),
   * `GAVE_UP` 은 그 밖의 이유(인증 등)입니다. 무대는 시작하지 않고 안내를 띄웁니다
   */
  const [failure, setFailure] = useState<{ key: string; reason: 'ENDED' | 'GAVE_UP' } | null>(null);
  const connected = socketKey !== null && readyKey === socketKey;
  const startFailure = failure !== null && failure.key === socketKey ? failure.reason : null;
  /** 지금 소켓으로 마이크 캡처를 거는 함수. 소켓 effect 가 넣고, 캡처 effect 가 부릅니다 */
  const startCaptureRef = useRef<(() => void) | null>(null);
  /** 캡처를 걸어도 되나 (`capture`). 소켓이 나중에 생기면 만들자마자 겁니다 */
  const captureWantedRef = useRef(capture);

  /** 소켓은 effect 안에서 한 번 만들어집니다. 그 뒤에 바뀐 콜백을 부르려고 ref 로 넘깁니다 */
  const onAutoEndRef = useRef(onAutoEnd);
  const restoredMetricsRef = useRef(restoredMetrics);
  const onClosedRef = useRef(onClosed);
  const onCoachRef = useRef(onCoach);
  const onConnectionRef = useRef(onConnection);
  useEffect(() => {
    onAutoEndRef.current = onAutoEnd;
    restoredMetricsRef.current = restoredMetrics;
    onClosedRef.current = onClosed;
    onCoachRef.current = onCoach;
    onConnectionRef.current = onConnection;
  });

  useEffect(() => {
    // 꺼져 있거나(종료 뒤) 스트림 · Take 가 없으면 붙지 않습니다 (`socketKey`)
    if (socketKey === null || !stream) return;

    let cancelled = false;
    /** 이 소켓이 `ready` 를 받았나. 받기 전의 실패는 '시작하지 못함'이라 무대가 따로 안내합니다 */
    let everReady = false;
    /** 소켓이 포기했다. 캡처가 그보다 늦게 준비되면 바로 놓습니다 */
    let abandoned = false;
    /** 캡처를 한 번만 겁니다 */
    let captureStarted = false;
    const key = socketKey;

    const created = new SttSocket({
      takeId,
      restoredMetrics: restoredMetricsRef.current?.(),
      slideHistory: () => slidesRef.current,
      // 전사는 화면에 띄우지 않습니다 — 서버에 쌓여 리포트에서 씁니다 (위 표)
      onTranscript: () => undefined,
      onState: (state) => {
        if (cancelled) return;
        setSttState(state);
        // 다시 붙었으면 지난 경고는 치웁니다 — 남아 있으면 계속 고장 난 것처럼 보입니다
        if (state === 'ok') setErrorNote(null);
      },
      onReady: () => {
        everReady = true;
        if (!cancelled) setReadyKey(key);
      },
      onStatus: (message) => {
        statsRef.current = { frames: message.frames, lostMs: message.lost_ms };
      },
      onError: (code, message, endReason) => {
        if (cancelled) return;
        if (code === 'TAKE_ENDED' && endReason === 'CLOSED') {
          onClosedRef.current?.(message);
          return;
        }
        if (!everReady) {
          // 시작하기 전에 끝난 Take 입니다 (시작하지 않은 채 정리됨 · 다른 곳에서 끝냄). 발표를
          // 시작하지 않았으니 이어 갈 것도 없습니다. 나머지 이유는 아래 onGiveUp 이 받습니다
          if (code === 'TAKE_ENDED') setFailure({ key, reason: 'ENDED' });
          return;
        }
        // 오래 끊겨 서버가 끝냈지만 발표는 이어 갑니다. 끝내기를 보내면 서버가 되살립니다
        setErrorNote(
          code === 'TAKE_ENDED' && endReason === 'ABANDONED'
            ? STT_ABANDONED_MESSAGE
            : STT_ERROR_MESSAGE[code],
        );
      },
      onConnection: (connected) => {
        if (!cancelled) onConnectionRef.current?.(connected);
      },
      onCoach: (message) => {
        if (!cancelled) onCoachRef.current?.(message);
      },
      onAutoEnd: (_reason, message) => {
        if (cancelled) return;
        // 서버가 보낸 문구가 이유를 말합니다 ("2분 넘게 말이 없어 …")
        setErrorNote(message);
        onAutoEndRef.current?.();
      },
      onGiveUp: (reason) => {
        // 보낼 곳이 없어졌습니다. 마이크 캡처를 계속 돌리면 남은 발표 내내
        // 오디오 스레드와 워클릿이 헛돕니다 — 같은 화면에서 시선 워커가 돌고 있습니다.
        // 캡처 준비가 이보다 늦게 끝날 수 있어 표시를 남깁니다 (아래 `startCapture`)
        abandoned = true;
        captureRef.current?.stop().catch(() => undefined);
        captureRef.current = null;

        if (cancelled) return;
        if (!everReady) {
          setFailure((current) => (current?.key === key ? current : { key, reason: 'GAVE_UP' }));
          return;
        }
        if (reason === 'FATAL') return;

        // FATAL 은 위 onError 가 이미 이유를 말했습니다. 나머지는 여기서 말합니다
        setErrorNote(
          reason === 'NO_TOKEN'
            ? STT_ERROR_MESSAGE.UNAUTHORIZED
            : '말하기 분석 연결이 끊겼어요. 발표는 계속하세요.',
        );
      },
    });
    socketRef.current = created;
    created.start();

    // 마이크 캡처는 시계가 돈 뒤에 겁니다 (`capture`). 소켓은 `ready` 전 오디오를 2초까지만 들고
    // 있고, 캡처는 시작할 때 시계를 읽어 기준점을 잡습니다
    const startCapture = () => {
      if (cancelled || abandoned || captureStarted) return;
      captureStarted = true;
      startPcmCapture(stream, {
        elapsedMs,
        onFrame: ({ pcm, offsetMs }) => created.sendFrame(pcm, offsetMs),
      })
        .then(({ capture: started, error }) => {
          // abandoned — 준비가 끝나기 전에 소켓이 포기한 경우입니다
          if (cancelled || abandoned || error) {
            started?.stop().catch(() => undefined);
            // 소켓은 닫지 않습니다 — 오디오만 못 갈 뿐 metrics 는 계속 보냅니다
            if (error && !cancelled) setCaptureNote(PCM_CAPTURE_ERROR_MESSAGE[error]);
            return;
          }
          captureRef.current = started;
        })
        .catch(() => {
          // 여기서 삼키면 화면에는 아무 표시 없이 말하기 분석만 사라집니다
          if (!cancelled) setCaptureNote(PCM_CAPTURE_ERROR_MESSAGE.WORKLET_FAILED);
        });
    };
    startCaptureRef.current = startCapture;
    if (captureWantedRef.current) startCapture();

    return () => {
      cancelled = true;
      startCaptureRef.current = null;
      captureRef.current?.stop().catch(() => undefined);
      captureRef.current = null;
      socketRef.current = null;
      created.dispose();
    };
  }, [socketKey, stream, takeId, elapsedMs]);

  useEffect(() => {
    captureWantedRef.current = capture;
    if (capture) startCaptureRef.current?.();
  }, [capture]);

  /**
   * 종료 CTA 가 **화면을 정리하기 전에** 부릅니다.
   *
   * 마이크를 먼저 끊고(더 보낼 것이 없다) `stop` 을 보낸 뒤 서버가 `closed` 를
   * 줄 때까지 기다립니다. 서버가 늦으면 3초에서 끊습니다 — `stop` 을 받은 서버는
   * 소켓이 닫혀도 마지막 전사까지 저장하므로, 종료 화면을 붙잡아 둘 이유가 없습니다.
   */
  const stop = useCallback(async () => {
    const capture = captureRef.current;
    captureRef.current = null;
    await capture?.stop().catch(() => undefined);

    const socket = socketRef.current;
    socketRef.current = null;
    await socket?.stop();
  }, []);

  /**
   * 1초 측정값을 보냅니다. 끊겨 있으면 소켓이 쌓아 두었다가 다시 붙을 때 보냅니다.
   * STT 를 켜지 않았으면(소켓이 없으면) 버립니다.
   */
  const sendMetrics = useCallback((message: MetricsMessage) => {
    socketRef.current?.sendMetrics(message);
  }, []);

  /**
   * 장이 바뀌었다 (`slide`). 기록에 먼저 남기고, 소켓이 붙어 있으면 바로 보냅니다.
   * 소켓이 아직 없거나 끊겨 있으면 다음 `ready` 때 기록 전체와 함께 갑니다 — 무대가 열리는 순간의
   * 첫 장 · 이어받은 기록은 대개 소켓보다 먼저 옵니다.
   */
  const sendSlide = useCallback((message: SlideMessage) => {
    slidesRef.current.push(message);
    socketRef.current?.sendSlide(message);
  }, []);

  /** 이 페이지에서 보낸 장 변경 전부. 새로고침 직전(`pagehide`)에 적어 두려고 꺼냅니다 (`saveSlides`) */
  const slideLog = useCallback((): SlideMessage[] => [...slidesRef.current], []);

  /**
   * 탭이 사라지기 직전(`pagehide`). 남은 1초들을 보내 보고, 서버에 닿았다고 볼 수 없는 것을 돌려줍니다
   * (`SttSocket.handOffMetrics`). 소켓이 없으면 `sendMetrics` 처럼 버립니다.
   */
  const handOffMetrics = useCallback(
    (messages: readonly MetricsMessage[]): HandedMetrics[] =>
      socketRef.current?.handOffMetrics(messages) ?? [],
    [],
  );

  return {
    sttState,
    connected,
    startFailure,
    /**
     * 발밑에 뜨는 한 줄. 에러가 있으면 에러가 이깁니다. 마이크가 실패했으면 연결 상태 문구
     * ("말하기 분석 중")는 맞지 않아 보여 주지 않습니다
     */
    note: errorNote ?? captureNote ?? (sttState ? STT_STATE_NOTE[sttState] : null),
    /** 눈에 띄게 할 것인가. 정상으로 도는 동안에는 조용히 둡니다 */
    alert:
      errorNote !== null ||
      captureNote !== null ||
      sttState === 'reconnecting' ||
      sttState === 'degraded',
    statsRef,
    sendMetrics,
    sendSlide,
    slideLog,
    handOffMetrics,
    stop,
  };
}
