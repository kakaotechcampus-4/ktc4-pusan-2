import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { LevelBar } from '@/features/rehearsal/media/LevelBar';
import { useCameraStream } from '@/features/rehearsal/media/useCameraStream';
import { useMicLevel } from '@/features/rehearsal/media/useMicLevel';
import { useVideoStream } from '@/features/rehearsal/media/useVideoStream';
import { usePrepareStore } from '@/features/rehearsal/prepare/prepareStore';
import { usePitchDetail, useCompleteTake, useTakeContext } from '@/shared/api/take';
import { buildGazePayload } from '@/features/rehearsal/lib/gazePayload';
import {
  beat,
  endSession,
  findSessionByTakeId,
  getSession,
  markGazeExcluded,
  readCoachLog,
  readGazeDecisions,
  readSlideChanges,
  setEngineVersion,
  setGazePerf,
  startSession,
} from '@/features/rehearsal/lib/db';
import { toMessage } from '@/shared/api/errorMessage';
import { ScreenLabel } from '@/shared/ui/ScreenLabel';
import { TemporalVoter } from '@/workers/temporalVoter';
import type { CompleteRequest, Ms } from '@/types/api';
import { ScriptPane } from './ScriptPane';
import { useCoach } from './useCoach';
import { useLiveGaze } from './useLiveGaze';
import { useRecording } from './useRecording';
import { useRehearsalStore } from './rehearsalStore';
import { useSlideDeck } from './useSlideDeck';
import { useStageClock } from './useStageClock';
import './stage.css';

/** 하트비트 주기. 탭이 죽으면 이 값이 멈춘 시각이 마지막 흔적입니다 */
const BEAT_MS = 5_000;

/**
 * 07 발표 연습 (P5 · P5x) — 리허설 무대.
 *
 * ── 이 화면이 지키는 것 ─────────────────────────────────────────────
 *
 * 1. **브라우저가 원본입니다.** 시선 판정·슬라이드 전환·코치 기록·녹음 조각이
 *    전부 IndexedDB에 먼저 쌓입니다. 서버로 가는 건 발표가 끝난 뒤입니다.
 *    중간에 탭이 죽어도 남은 기록으로 리포트를 만들 수 있어야 합니다.
 *
 * 2. **초당 한 번 바뀌는 값은 React를 거치지 않습니다.** 시계·음량·녹음 크기·
 *    시선 테두리는 전부 DOM에 직접 씁니다. 상태로 올리는 것은 슬라이드 번호와
 *    코치 메시지뿐입니다 — 둘 다 사람이 움직일 때만 바뀝니다.
 *
 * 3. **끝내기는 되돌릴 수 없습니다.** 숫자는 종료 시점에 영구 고정되고
 *    (서버는 시선을 재계산할 수 없습니다) 그래서 한 번 더 묻습니다.
 *
 * ── 아직 없는 것 ────────────────────────────────────────────────────
 * 2단(서버) 코치 — 속도·군더더기·대본 일치. WebSocket이 붙는 날 코치 줄에
 * 같이 들어옵니다. 1단이 그것과 무관하게 돈다는 게 이 구조의 요점입니다.
 */
export function RehearsalPage() {
  const { takeId = '' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const take = useTakeContext(takeId);
  const pitch = usePitchDetail(take.data?.pitchId);
  const complete = useCompleteTake(takeId);

  const phase = useRehearsalStore((s) => s.phase);
  const coach = useRehearsalStore((s) => s.coach);
  const setPhase = useRehearsalStore((s) => s.setPhase);
  const resetStore = useRehearsalStore((s) => s.reset);

  /** 준비 화면에서 잡은 기준. 새로고침으로 돌아왔으면 없습니다(= null로 보냅니다) */
  const calibration = usePrepareStore((s) => s.calibration);
  const gazeDeclined = usePrepareStore((s) => s.gazeDeclined);

  const stageRef = useRef<HTMLDivElement>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [endError, setEndError] = useState<string | null>(null);

  const { stream, error: deviceError, request } = useCameraStream();
  const { videoRef, live } = useVideoStream(stream, 'rehearsal');
  const { meterRef, dbRef, statsRef, audioState } = useMicLevel(stream);

  const ready = take.data !== undefined && pitch.data !== undefined;
  const running = ready && phase === 'RUNNING';
  const limitSec = take.data?.timeLimitSec ?? pitch.data?.timeLimitSec ?? 600;
  const scriptMode = take.data?.scriptMode ?? 'HIGHLIGHT';
  const mode = take.data?.mode ?? 'COACHING';
  const slides = pitch.data?.presentation.slides ?? [];

  const { elapsedRef, limitRef, barRef, fillRef, remainRef, elapsedMs } = useStageClock(
    limitSec,
    running,
  );
  const {
    ready: gazeReady,
    engineVersion,
    error: gazeError,
    perf,
    bottomRatio,
  } = useLiveGaze({
    stream,
    videoRef,
    stageRef,
    clientSessionId: sessionId,
    // live 까지 봅니다 — 스트림 객체만 있고 아직 프레임이 없을 때 펌프를 돌리면
    // 워커가 "카메라 소실"로 읽고 스스로 멈춥니다
    enabled: running && !gazeDeclined && live,
  });
  const { slideNumber, slideStartedAtRef } = useSlideDeck({
    total: slides.length,
    clientSessionId: sessionId,
    elapsedMs,
    enabled: running,
  });
  const {
    sizeRef,
    recording,
    errorMessage: recErrorMessage,
    stop: stopRecording,
  } = useRecording({ stream, clientSessionId: sessionId, enabled: running });

  useCoach({
    enabled: running,
    mode,
    clientSessionId: sessionId,
    limitSec,
    elapsedMs,
    bottomRatio,
    statsRef,
    audioLive: audioState === 'running',
    slideStartedAtRef,
  });

  // ── 세션 잇기 ────────────────────────────────────────────────────
  // 준비 화면이 넘겨준 값이 먼저입니다. 새로고침이면 takeId로 IndexedDB를 뒤지고,
  // 그래도 없으면(이 주소로 바로 들어온 경우) 새로 엽니다.
  const resolvedRef = useRef(false);
  useEffect(() => {
    if (resolvedRef.current || takeId === '') return;
    resolvedRef.current = true;

    (async () => {
      const passed = (location.state as { clientSessionId?: string } | null)?.clientSessionId;
      if (passed) {
        setSessionId(passed);
        return;
      }
      const row = await findSessionByTakeId(takeId);
      setSessionId(row ? row.clientSessionId : await startSession(takeId));
    })().catch(() => undefined);
  }, [takeId, location.state]);

  // 화면을 떠날 때 다음 Take를 위해 무대 상태를 비웁니다
  useEffect(() => resetStore, [resetStore]);

  // 카메라는 준비 화면 CTA를 누른 직후라 바로 열립니다 (같은 문서 = 조작이 살아 있음)
  const askedRef = useRef(false);
  useEffect(() => {
    if (askedRef.current || gazeDeclined) return;
    askedRef.current = true;
    request().catch(() => undefined);
  }, [request, gazeDeclined]);

  // ── 제외 사유 배선 ───────────────────────────────────────────────
  // 한 번 정해지면 되돌리지 않습니다. 발표 도중 엔진이 죽었다면 그 Take의
  // 시선 숫자는 앞뒤가 다른 조건에서 나온 것이라 믿을 수 없습니다.
  useEffect(() => {
    if (!sessionId) return;
    if (gazeDeclined) markGazeExcluded(sessionId, 'USER_DECLINED').catch(() => undefined);
  }, [sessionId, gazeDeclined]);

  useEffect(() => {
    if (!sessionId || !deviceError) return;
    markGazeExcluded(
      sessionId,
      deviceError === 'PERMISSION_DENIED' ? 'USER_DECLINED' : 'CAMERA_LOST',
    ).catch(() => undefined);
  }, [sessionId, deviceError]);

  useEffect(() => {
    if (!sessionId || !gazeError) return;
    markGazeExcluded(sessionId, gazeError).catch(() => undefined);
  }, [sessionId, gazeError]);

  // 엔진 버전은 종료 시점에 영구 고정됩니다 — 받는 즉시 기록해 둡니다
  useEffect(() => {
    if (!sessionId || !engineVersion) return;
    setEngineVersion(sessionId, engineVersion).catch(() => undefined);
  }, [sessionId, engineVersion]);

  useEffect(() => {
    if (!sessionId || !perf) return;
    setGazePerf(sessionId, perf.avgFps, perf.droppedFrames).catch(() => undefined);
  }, [sessionId, perf]);

  // 하트비트 — 벽시계로 찍습니다. 탭이 죽으면 이 값이 마지막 흔적이 되고,
  // 다음에 앱을 열었을 때 "진행 중이던 Take가 있다"를 이걸로 압니다
  useEffect(() => {
    if (!running || !sessionId) return;
    const id = window.setInterval(
      () => beat(sessionId, elapsedMs()).catch(() => undefined),
      BEAT_MS,
    );
    return () => window.clearInterval(id);
  }, [running, sessionId, elapsedMs]);

  // 시선 판정이 아직 없는 dev 환경에서 테두리 3색을 눈으로 보려고 둡니다.
  // 1·2·3 키. **기록에는 남기지 않습니다** — 판정이 아니라 눈속임입니다
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const onKey = (e: KeyboardEvent) => {
      const zone = { '1': 'AUDIENCE', '2': 'SCREEN', '3': 'UNKNOWN' }[e.key];
      if (zone && stageRef.current) stageRef.current.dataset.gaze = zone;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const paragraphs = useMemo(
    () => (pitch.data?.script.content ?? '').split('\n\n').filter((p) => p.trim() !== ''),
    [pitch.data?.script.content],
  );

  /** 지금 슬라이드의 문단. 앵커가 없으면 슬라이드 번호를 그대로 씁니다 */
  const currentIndex = useMemo(() => {
    const anchors = pitch.data?.script.slideAnchors ?? [];
    const anchor = anchors.find((a) => a.slideNumber === slideNumber);
    if (!anchor) return Math.min(slideNumber - 1, paragraphs.length - 1);
    let acc = 0;
    for (let i = 0; i < paragraphs.length; i++) {
      if (acc >= anchor.charOffset) return i;
      acc += paragraphs[i]!.length + 2;
    }
    return paragraphs.length - 1;
  }, [pitch.data?.script.slideAnchors, slideNumber, paragraphs]);

  const currentSlide = slides.find((s) => s.slideNumber === slideNumber);
  const nextSlideNumber = Math.min(slideNumber + 1, slides.length);

  // ── 종료 ─────────────────────────────────────────────────────────
  const finish = async () => {
    if (!running || !sessionId || !take.data) return;

    // 시간을 먼저 붙잡습니다. 아래 await들이 도는 동안에도 시계는 갑니다
    const durationMs: Ms = elapsedMs();

    // 마지막 5초 조각까지 받고 멈춥니다. 이걸 기다리지 않으면 끝말이 잘립니다
    await stopRecording();
    setPhase('ENDING');
    await endSession(sessionId);

    const row = await getSession(sessionId);
    const decisions = await readGazeDecisions(sessionId);
    const changes = await readSlideChanges(sessionId);
    const coachRows = await readCoachLog(sessionId);

    const gazePayload = buildGazePayload({
      decisions,
      durationMs,
      engineVersion: row?.engineVersion ?? null,
      decisionIntervalMs: TemporalVoter.INTERVAL_MS,
      excludedReason: row?.gazeExcluded ? row.gazeExcludedReason : null,
      calibration,
    });

    const body: CompleteRequest = {
      clientSessionId: sessionId,
      startedAt: row?.startedAtIso ?? new Date(Date.now() - durationMs).toISOString(),
      endedAt: new Date().toISOString(),
      durationMs,
      mode,
      // 패널 숨김 UI는 아직 없습니다. 생기면 여기에 그 목록이 들어갑니다
      hiddenPanels: [],
      slideEvents: toSlideEvents(changes, durationMs),
      gaze: gazePayload.payload,
      liveFeedbacks: coachRows
        .filter((c) => c.fired)
        .map((c) => ({
          type: c.type,
          message: c.message ?? '',
          triggeredAtMs: c.atMs,
          // 1단은 규칙이라 확률이 없습니다. 2단(모델)이 붙으면 그쪽이 값을 채웁니다
          confidence: 1,
        })),
      suppressedFeedbacks: coachRows
        .filter((c) => !c.fired)
        .map((c) => ({ type: c.type, atMs: c.atMs, reason: c.suppressedReason ?? 'UNKNOWN' })),
      // ★ 오디오 업로드 경로가 아직 없습니다. 조각은 IndexedDB에 있고,
      //   업로드가 붙으면 그 키가 여기 들어옵니다 (실패 시 P15 재시도 화면).
      audioFileKey: '',
      clientPerf: {
        avgGazeFps: row?.gazeAvgFps ?? 0,
        droppedFrames: row?.gazeDroppedFrames ?? 0,
        // LIGHT 강등 임계값은 I-03이 정해져야 만들 수 있습니다
        degradedToLightAtMs: null,
      },
    };

    try {
      await complete.mutateAsync(body);
      navigate(`/takes/${takeId}/processing`);
    } catch (e) {
      // 기록은 브라우저에 그대로 있습니다. 여기서 잃는 것은 없고,
      // 재시도 화면이 같은 clientSessionId로 다시 보냅니다
      console.error('[rehearsal] complete failed', e);
      setEndError(toMessage(e));
      navigate(`/takes/${takeId}/retry`, { state: { clientSessionId: sessionId } });
    }
  };

  const gazeNote = gazeDeclined
    ? '시선 측정 제외 · 소리만으로 진행 중'
    : deviceError
      ? '카메라가 끊겼습니다 — 발표는 계속됩니다'
      : gazeError
        ? `시선 측정 제외 · ${gazeError}`
        : gazeReady
          ? '시선 기록 중'
          : '시선 엔진 준비 중';

  return (
    <div className="min-h-full bg-greige px-4 py-5">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
        <ScreenLabel
          screenNo="07"
          screenName="발표 연습"
          entry={`진입 · 리허설 준비의 Take ${take.data?.takeNumber ?? ''} 시작하기`}
        />

        <div className="h-[calc(100vh-7rem)] min-h-[560px] overflow-hidden rounded-2xl shadow-lg">
          {/* ★ data-gaze 한 줄이 테두리의 전부입니다. 워커의 1초 판정이 여기 도착합니다 */}
          <div
            className="stage"
            ref={stageRef}
            data-script-mode={scriptMode}
            data-gaze="UNKNOWN"
            data-mode={mode}
          >
            <header className="stage-head">
              <span className="elapsed" ref={elapsedRef}>
                00:00
              </span>
              <span className="limit">
                /{' '}
                <span ref={limitRef} className="tabular">
                  10:00
                </span>
              </span>
              <div className="bar" ref={barRef} data-over="false">
                <i ref={fillRef} />
              </div>
              <span className="meta" ref={remainRef}>
                남은 —
              </span>
              <span className="slide-no">
                SLIDE {slideNumber} / {slides.length || '—'}
              </span>
            </header>

            <div className="viewport">
              <div className="slide">
                {currentSlide?.imageUrl ? (
                  <img src={currentSlide.imageUrl} alt={`슬라이드 ${slideNumber}`} />
                ) : (
                  <span className="placeholder">SLIDE {slideNumber} · 16:9</span>
                )}
                {!currentSlide?.imageUrl && (
                  <span className="note">자료 이미지가 아직 없습니다</span>
                )}
              </div>

              <div className="rail">
                <section className="camera">
                  <video ref={videoRef} muted playsInline />
                  {!live && <div className="guide" />}
                  <span className="tag">CAMERA 16:9</span>
                </section>

                <section className="mic">
                  <LevelBar variant="segments" meterRef={meterRef} dbRef={dbRef} />
                </section>

                <section className="next">
                  <div className="thumb">SLIDE {nextSlideNumber} · 16:9</div>
                  <div className="label">
                    <b>다음 슬라이드</b>
                    <span>→ 키로 이동</span>
                  </div>
                </section>

                {/* 배지를 따로 두지 않습니다 — 테두리가 시선 표시의 전부입니다.
                    대신 색이 무슨 뜻인지만 한 줄로 둡니다 (CLAUDE.md 3번) */}
                <div className="legend">
                  <span>테두리 =</span>
                  <i className="audience">청중</i>
                  <i className="screen">화면</i>
                  <i className="unknown">판정 불가</i>
                </div>
              </div>
            </div>

            <div className="coach" data-empty={coach === null}>
              <span>{coach?.text ?? ''}</span>
              <span className="rule">한 번에 하나만 · 8초 후 사라짐</span>
            </div>

            <ScriptPane
              mode={scriptMode}
              paragraphs={paragraphs}
              currentIndex={currentIndex}
              keywords={(currentSlide?.keywords ?? []).map((k) => k.text)}
            />

            <div className="stage-foot">
              <span className="rec" data-on={recording}>
                {recording ? '기록 중 · ' : '기록 멈춤 · '}
                <span ref={sizeRef} className="tabular">
                  0.0MB
                </span>
              </span>
              <span>{gazeNote}</span>
              {audioState !== null && audioState !== 'running' && (
                <span>소리가 흐르지 않습니다 — 화면을 한 번 클릭해 주세요</span>
              )}
              {mode === 'EXAM' && <span>실전 모드 — 발표 중에는 코치가 말하지 않습니다</span>}
              {recErrorMessage && <span>{recErrorMessage}</span>}
              {endError && <span>{endError}</span>}

              <button
                type="button"
                className="end"
                disabled={!running || phase !== 'RUNNING'}
                onClick={() => {
                  // 되돌릴 수 없어서 한 번 더 묻습니다. 모달을 띄우지 않는 이유는
                  // 발표 중에 화면을 덮으면 남은 시간을 못 보기 때문입니다
                  if (!confirming) {
                    setConfirming(true);
                    window.setTimeout(() => setConfirming(false), 4000);
                    return;
                  }
                  finish().catch(() => undefined);
                }}
              >
                {phase === 'ENDING'
                  ? '정리하는 중…'
                  : confirming
                    ? '정말 끝낼까요?'
                    : '발표 끝내기'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * 전환 시각 목록을 구간으로 접습니다.
 * 마지막 슬라이드의 끝은 발표 길이입니다 — 그래야 구간의 합이 발표 전체가 됩니다.
 */
function toSlideEvents(
  changes: { atMs: Ms; slideNumber: number }[],
  durationMs: Ms,
): { slideNumber: number; startMs: Ms; endMs: Ms }[] {
  return changes.map((c, i) => ({
    slideNumber: c.slideNumber,
    startMs: c.atMs,
    endMs: changes[i + 1]?.atMs ?? durationMs,
  }));
}
