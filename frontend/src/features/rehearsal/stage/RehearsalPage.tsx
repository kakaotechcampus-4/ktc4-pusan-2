import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { LevelBar } from '../media/LevelBar';
import { MIC_ERROR_MESSAGE, useCameraStream } from '../media/useCameraStream';
import { useMicLevel } from '../media/useMicLevel';
import { useVideoStream } from '../media/useVideoStream';
import { usePrepareStore } from '../prepare/prepareStore';
import { useAbandonTake, useEndTake } from '@/shared/api/take';
import { ApiFailure } from '@/shared/api/tokenStore';
import { Button, ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card } from '@/shared/ui/Panel';
import { PdfPage } from '@/shared/ui/PdfPage';
import { exclusionForDeviceError, exclusionForGazeError } from '../lib/gazeExclusion';
import { stopTakeStream } from '../lib/sttSocket';
import { forgetZoneRef } from '../lib/zoneRefMemory';
import {
  beat,
  endSession,
  findSessionByTakeId,
  getSession,
  markEnding,
  markGazeExcluded,
  readGazeSamples,
  readSlideChanges,
  setEngineVersion,
  setGazePerf,
  startSession,
} from '../lib/db';
import { noteWriteFailure } from '../lib/writeFailures';
import { toMessage } from '@/shared/api/errorMessage';
import { formatDuration } from '@/shared/lib/clock';
import type { GazeExcludedReason, Ms, RehearsalTicket } from '@/types/api';
import { ScriptPane } from './ScriptPane';
import { CoachStrip } from './CoachStrip';
import { useLiveCoach } from './useLiveCoach';
import { useLiveGaze } from './useLiveGaze';
import { useRehearsalMaterials } from './useRehearsalMaterials';
import { useRehearsalStore, type Indicators } from './rehearsalStore';
import { indicatorViews } from '../lib/coachIndicators';
import { gazeNote } from '../lib/gazeNote';
import {
  abandonOutcome,
  clearClock,
  clearMetrics,
  clearSlides,
  lastSlide,
  leftWindow,
  readClock,
  recordedUntilMs,
  replacedElsewhere,
  resumeFromMs,
  mergeSlideChanges,
  saveClock,
  saveMetrics,
  saveSlides,
  takeMetrics,
  takeSlides,
  type AbandonOutcome,
  type LeftWindow,
} from './resume';
import { canEnd } from './canEnd';
import { sendEnding, type Ending } from './sendEnding';
import { useEndConfirm } from './useEndConfirm';
import { useSlideDeck } from './useSlideDeck';
import { useStageClock } from './useStageClock';
import { CONNECT_SLOW_MS, hasCamera, hasLiveAudio, startGate, type StartGate } from './startGate';
import { openStageDevices } from './openDevices';
import { useSttStream } from './useSttStream';
import { useMetricsSender } from './useMetricsSender';
import { useSlideSender } from './useSlideSender';
import type { TextRange } from '../lib/scriptMarks';
import './stage.css';

/** 하트비트 주기. 탭이 죽으면 이 값이 멈춘 시각이 마지막 흔적입니다 */
const BEAT_MS = 5_000;

/** 대본이 없는 장의 기본값. 매 렌더 새 배열을 만들면 대본 칸의 강조 계산이 매번 다시 돕니다 */
const NO_KEYWORDS: string[] = [];
const NO_HIGHLIGHTS: TextRange[] = [];

/**
 * 이 Take 를 어디서부터 여나. 세션 행을 읽어야 정해지고, 그 전에는 무대를 시작하지 않습니다 —
 * 시계가 0 으로 먼저 출발하면 새로고침 전 기록과 시간대가 겹칩니다 (`resume.ts`).
 */
interface Resume {
  /** 세션을 못 열었으면 null — 기록은 안 쌓여도 발표는 합니다 */
  sessionId: string | null;
  /** 무대 시계를 여기서부터 돌립니다. 처음이면 0 */
  fromMs: Ms;
  /** 이어받은 슬라이드로 넘어온 시각. 처음이면 null */
  slideAt: Ms | null;
  /** 이어받은 장 넘김 기록 전체. 처음이거나 못 읽었으면 비어 있습니다 — WS `slide` 로 다시 보냅니다 */
  slideHistory: { atMs: Ms; slideNumber: number }[];
  /** 끝내기를 누른 뒤에 새로고침했으면 그때 고정한 값. 무대를 열지 않고 종료를 이어갑니다 */
  ending: Ending | null;
  /** 종료까지 보낸 Take. 무대를 열지 않고 처리 화면으로 보냅니다 */
  submitted: boolean;
  /**
   * 이 창에서 무대를 열면 안 되는 이유 (`leftWindow`). 창을 닫았다 다시 열었으면 `CLOSED` —
   * 실패로 끝냅니다. 다른 창이 발표 중이면 `OTHER_WINDOW` — 아무것도 바꾸지 않습니다
   */
  left: LeftWindow | null;
  /** `CLOSED` 중에서도 다른 곳에서 새 Take 를 시작해 끝난 것 (`replacedElsewhere`) */
  replaced: boolean;
}

/** 이어받은 장 넘김 기록이 없을 때. 렌더마다 새 배열을 만들지 않게 둡니다 */
const NO_SLIDES: Resume['slideHistory'] = [];

const freshResume = (sessionId: string | null): Resume => ({
  sessionId,
  fromMs: 0,
  slideAt: null,
  slideHistory: [],
  ending: null,
  submitted: false,
  left: null,
  replaced: false,
});

/**
 * 07 발표 연습 (P5 · P5x) — 리허설 무대.
 *
 * ── 이 화면이 지키는 것 ─────────────────────────────────────────────
 *
 * 1. **브라우저가 원본입니다.** 시선 판정·슬라이드 전환이
 *    전부 IndexedDB에 먼저 쌓입니다. 서버로 가는 건 발표가 끝난 뒤입니다.
 *    음성만 예외입니다 — 로컬에 남기지 않고 WebSocket으로만 흘립니다 (CLAUDE.md 4번).
 *    중간에 탭이 죽어도 남은 기록으로 리포트를 만들 수 있어야 합니다.
 *
 * 2. **초당 한 번 바뀌는 값은 React를 거치지 않습니다.** 시계·음량·
 *    시선 테두리는 전부 DOM에 직접 씁니다. 상태로 올리는 것은 슬라이드 번호와
 *    코치 메시지뿐입니다 — 둘 다 사람이 움직일 때만 바뀝니다.
 *
 * 3. **끝내기는 되돌릴 수 없습니다.** 숫자는 종료 시점에 영구 고정되고
 *    (서버는 시선을 재계산할 수 없습니다) 그래서 한 번 더 묻습니다.
 *
 * ── 실시간 코치 ──────────────────────────────────────────────────────
 * 판단은 AI 코치 에이전트가 합니다(`/coach/evaluate`, BE 가 부름). FE 는 WS `coach` 로 받은
 * 한마디와 상태 표시를 띄우기만 합니다(`useLiveCoach`). FE 가 따로 판정하지 않습니다 —
 * 기준이 두 곳에 있으면 화면의 말과 리포트의 숫자가 어긋납니다 (`docs/ai/architecture.md` 0절).
 */
export function RehearsalPage() {
  const { takeId = '' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  /**
   * 무대를 여는 데 필요한 값. 장치 점검이 넘겨주고, 새로고침이면 IndexedDB 세션에서 꺼냅니다.
   * BE 에 Take 를 읽는 API 가 없어서 서버에서 다시 받을 길이 없습니다.
   * `undefined` 는 아직 찾는 중, `null` 은 어디에도 없음입니다.
   */
  const [ticket, setTicket] = useState<RehearsalTicket | null | undefined>(
    () => (location.state as { ticket?: RehearsalTicket } | null)?.ticket,
  );
  const materials = useRehearsalMaterials(ticket ?? null);
  const endTake = useEndTake(ticket?.pitchId ?? '', takeId);
  const { mutateAsync: abandonTake } = useAbandonTake(ticket?.pitchId ?? '', takeId);

  const phase = useRehearsalStore((s) => s.phase);
  const coach = useRehearsalStore((s) => s.coach);
  const indicators = useRehearsalStore((s) => s.indicators);
  const indicatorsStale = useRehearsalStore((s) => s.indicatorsStale);
  const setPhase = useRehearsalStore((s) => s.setPhase);
  const setSlide = useRehearsalStore((s) => s.setSlide);
  const resetStore = useRehearsalStore((s) => s.reset);

  /** 준비 화면에서 잡은 기준. 새로고침하면 세션 행에서 되살립니다. 거기도 없으면 null로 보냅니다 */
  const calibration = usePrepareStore((s) => s.calibration);
  /** 점검을 통과한 장치. 새로고침하면 세션 행에서 되살립니다. 거기도 없으면 기본 장치를 엽니다 */
  const devices = usePrepareStore((s) => s.devices);
  const restorePrepare = usePrepareStore((s) => s.restore);

  const stageRef = useRef<HTMLDivElement>(null);

  /**
   * 정해진 제외 사유. **IndexedDB 와 별개로** 들고 있습니다.
   *
   * DB 는 새로고침을 견디고, 이 ref 는 **DB 쓰기가 실패해도 남습니다.**
   * 제외 표시를 놓치면 믿을 수 없는 숫자가 정상인 척 리포트에 실리므로,
   * 둘 다 잃는 경우에만 그 일이 벌어지게 해 둡니다.
   *
   * `??=` 인 이유는 **먼저 정해진 사유가 이깁니다** — 카메라가 끊긴 뒤에
   * 저장까지 실패했다고 사유가 STORAGE_FAILED 로 덮이면 원인이 뒤바뀝니다.
   *
   * ★ 지금은 읽는 곳이 없습니다. 예전에는 `/complete` 의 시선 제외 사유로 보냈는데, 그 경로가
   *   없어졌습니다(#134). 제외 사유를 서버에 어떻게 알릴지 BE 와 정하면 거기서 읽습니다.
   */
  const excludedRef = useRef<GazeExcludedReason | null>(null);
  const noteExclusion = useCallback((reason: GazeExcludedReason) => {
    excludedRef.current ??= reason;
  }, []);
  const [resume, setResume] = useState<Resume | null>(null);
  const sessionId = resume?.sessionId ?? null;
  const ending = resume?.ending ?? null;
  const [endError, setEndError] = useState<string | null>(null);
  /**
   * 세션을 못 연 이유. 이게 있으면 이 Take 의 기록(시선·슬라이드·코치)이 하나도 쌓이지 않고
   * 종료 버튼도 아무것도 하지 않습니다 — 조용히 넘어가면 발표를 다 하고 나서야 압니다.
   */
  const [sessionError, setSessionError] = useState<string | null>(null);

  const {
    stream,
    cameraError,
    micError,
    request,
    requestMic,
    stop: stopCamera,
  } = useCameraStream();
  const { videoRef, live } = useVideoStream(stream, 'rehearsal');
  /** 카메라를 못 열어 마이크만 열었으면 false 입니다. 발표는 하고 시선만 뺍니다 */
  const cameraOpened = hasCamera(stream);
  /**
   * 장치를 차례로 여는 중 (`openDevices.ts`). 최종 결과가 나오기 전에는
   * 중간 오류로 시선을 제외하거나 장치 오류 안내를 띄우지 않습니다
   */
  const [opening, setOpening] = useState(false);

  const ready = materials.ready && resume !== null;
  /** 이미 종료까지 보낸 Take 입니다 (끝낸 뒤 새로고침 · 뒤로 가기). 무대를 열지 않습니다 */
  const submitted = resume?.submitted ?? false;
  const left = resume?.left ?? null;
  /**
   * 무대를 열 수 있나. 종료 중에 새로고침했으면 열지 않고 종료만 이어갑니다.
   * 창을 닫았다 다시 열었거나 다른 창이 발표 중이면 열지 않습니다
   */
  const stageReady = ready && ending === null && !submitted && left === null;
  /**
   * 발표를 시작했나 (`startGate`). 마이크를 열고 서버 연결이 첫 `ready` 를 준 뒤에 true 가 되고,
   * 그 뒤로는 끝까지 true 입니다. 시계 · 기록 · 끝내기 버튼은 모두 이걸 기다립니다.
   * 아래 `useSttStream` 이 `ready` 를 알려 주므로 한 렌더 늦게 바뀝니다
   */
  const [started, setStarted] = useState(false);
  const running = stageReady && phase === 'RUNNING' && started;
  /** 첫 연결이 안 될 때 '다시 시도'가 올립니다. 소켓을 새로 만듭니다 */
  const [connectAttempt, setConnectAttempt] = useState(0);
  /** `ready` 를 `CONNECT_SLOW_MS` 넘게 기다렸다 */
  const [connectSlow, setConnectSlow] = useState(false);
  /** 끝내기가 `TAKE_NOT_STARTED` 를 받았다 — 서버에서 시작하지 않은 Take 입니다 */
  const [notStarted, setNotStarted] = useState(false);
  const limitSec = ticket?.timeLimitSec ?? 600;
  const scriptMode = ticket?.scriptMode ?? 'HIGHLIGHT';
  const mode = ticket?.mode ?? 'COACHING';
  const pageCount = materials.pageCount;

  const { elapsedRef, limitRef, barRef, fillRef, remainRef, elapsedMs } = useStageClock(
    limitSec,
    running,
    resume?.fromMs ?? 0,
  );
  // 음량 1초 기록을 무대 시계로 쌓습니다 — 시선 1초 기록과 같은 시계라야 같은 1초를 가리킵니다.
  // 기록은 `voiceRecord(t_ms, t_ms + duration_ms)` 로 꺼내 시선 기록과 함께 WS `metrics` 로 보냅니다 (`useMetricsSender`)
  const { meterRef, audioState, voiceRecord } = useMicLevel(stream, {
    clock: elapsedMs,
  });
  const { slideNumber, slideStartedAtRef, slideAt } = useSlideDeck({
    total: pageCount,
    clientSessionId: sessionId,
    elapsedMs,
    enabled: running,
    resumedAt: resume?.slideAt ?? null,
  });
  /**
   * 서버가 Take 를 자동으로 끝냈을 때(말 없음 · 최대 시간) 끝내기 버튼과 같은 흐름을 탑니다.
   * `finish` 는 아래에서 매 렌더 새로 만들어지는 함수라 ref 로 최신 것을 부릅니다.
   */
  const finishRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const onAutoEnd = useCallback(() => {
    finishRef.current().catch((e: unknown) => {
      console.error('[rehearsal] 자동 종료 처리 실패', e);
      setEndError(toMessage(e));
    });
  }, []);
  /**
   * 창을 닫아 끝난 Take 입니다. 무대를 닫고 실패로 끝냅니다 (아래 '창을 닫았다 다시 열었다').
   * BE 문구가 있으면 다른 곳에서 새 Take 를 시작해 끝났는지 가려 안내를 바꿉니다
   */
  const leaveClosed = useCallback((message?: string) => {
    setResume(
      (current) => current && { ...current, left: 'CLOSED', replaced: replacedElsewhere(message) },
    );
  }, []);
  // 서버가 보낸 AI 코치의 한마디 · 상태 표시를 띄웁니다
  const { onCoach, onConnection } = useLiveCoach({ enabled: running, mode });
  /**
   * 2단 코치의 입력선. `ENDING`에도 살려 두는 이유는 종료 CTA가 `stop`을 보내고
   * 서버의 `closed`를 기다려야 하기 때문입니다 — 여기서 끊으면 마지막 문장이 사라집니다.
   */
  // 새로고침 전 페이지가 못 보내고 적어 둔 `metrics`. 소켓이 붙자마자 먼저 보냅니다 (resume.ts)
  const restoredMetrics = useCallback(
    () => (sessionId ? takeMetrics(takeId, sessionId) : []),
    [takeId, sessionId],
  );
  const {
    connected,
    startFailure,
    note: sttNote,
    alert: sttAlert,
    sendMetrics,
    sendSlide,
    handOffMetrics,
    slideLog,
    stop: stopStt,
  } = useSttStream({
    takeId,
    stream,
    // 시계보다 먼저 붙습니다 — 첫 `ready` 를 받아야 발표를 시작합니다
    enabled: stageReady && (phase === 'RUNNING' || phase === 'ENDING'),
    // 마이크는 시계가 돈 뒤에 겁니다. 종료 중에도 살려 둡니다 — 끝내기가 `stop` 으로 직접 끊습니다
    capture: stageReady && started,
    attempt: connectAttempt,
    elapsedMs,
    onAutoEnd,
    restoredMetrics,
    onClosed: leaveClosed,
    onCoach,
    onConnection,
  });

  const gate = startGate({
    started,
    // 새로고침으로 이어 가는 Take 는 서버에서 이미 시작했습니다
    resumed: (resume?.fromMs ?? 0) > 0,
    micLive: hasLiveAudio(stream),
    micError: opening ? null : micError,
    connected,
    startFailure,
    slow: connectSlow,
  });
  // 시작했으면 붙잡아 둡니다. 렌더 중에 바꿔 effect 를 한 번 더 돌리지 않습니다 — 그 뒤로는
  // 마이크가 빠지거나 연결이 끊겨도 시계를 멈추지 않습니다
  if (!started && stageReady && gate.kind === 'STARTED') setStarted(true);
  // `ready` 를 오래 기다리면 다시 시도를 띄웁니다. 다시 시도하면 처음부터 셉니다
  useEffect(() => {
    if (!stageReady || gate.kind !== 'CONNECTING') return;
    const id = window.setTimeout(() => setConnectSlow(true), CONNECT_SLOW_MS);
    return () => window.clearTimeout(id);
  }, [stageReady, gate.kind, connectAttempt]);

  // 장이 바뀐 시각을 WS `slide` 로 알립니다. 서버가 장 구간을 나누는 기준입니다 (#324 6-4)
  useSlideSender({
    enabled: running,
    slideNumber,
    slideStartedAtRef,
    history: resume?.slideHistory ?? NO_SLIDES,
    send: sendSlide,
  });
  // 1초마다 시선 · 음량 · 슬라이드(참고값)를 WS `metrics` 로 보냅니다. 서버의 실시간 코치와 Take 결과의 재료입니다
  const { onGazeRecords, finishMetrics, flushMetricsForHide } = useMetricsSender({
    enabled: running,
    elapsedMs,
    voiceRecord,
    slideAt,
    send: sendMetrics,
  });
  const {
    ready: gazeReady,
    engineVersion,
    error: gazeError,
    perf,
    missingCalibration,
    flushGaze,
  } = useLiveGaze({
    stream,
    videoRef,
    stageRef,
    clientSessionId: sessionId,
    // 장치 점검에서 저장한 기준을 이 키로 꺼내 리허설 워커에 넣습니다
    layoutSignature: calibration?.layoutSignature ?? null,
    // 판정 저장이 실패해 제외가 정해지면 메모리에도 받아 둡니다
    onExcluded: noteExclusion,
    elapsedMs,
    // live 까지 봅니다 — 스트림 객체만 있고 아직 프레임이 없을 때 펌프를 돌리면
    // 워커가 "카메라 소실"로 읽고 스스로 멈춥니다. 마이크만 연 스트림도 같은 이유로 돌리지 않습니다
    enabled: running && live && cameraOpened,
    onRecords: onGazeRecords,
  });

  // ── 세션 잇기 ────────────────────────────────────────────────────
  // 준비 화면이 넘겨준 값이 먼저입니다. 없으면(다른 창에서 이 주소를 열었다) takeId로
  // IndexedDB를 뒤지고, 그래도 없으면 새로 엽니다. 다른 창에서 찾은 행은 이어 가지 않습니다 —
  // 창을 닫았다 다시 열었거나, 다른 창이 아직 발표 중입니다 (`leftWindow`).
  //
  // ★ 새로고침해도 location.state 는 남습니다. 그래서 막 넘어왔는지 새로고침했는지는
  //   행을 읽어야 압니다 — 두 길 모두 행을 읽고, 이어받을 것(시계·슬라이드·준비 값·
  //   종료 중이었나)을 모은 뒤에 무대를 엽니다.
  const resolvedRef = useRef(false);
  useEffect(() => {
    if (resolvedRef.current || takeId === '') return;
    resolvedRef.current = true;

    const passed = (location.state as { clientSessionId?: string } | null)?.clientSessionId;
    (async () => {
      const row = passed ? await getSession(passed) : await findSessionByTakeId(takeId);
      const leftReason = leftWindow(row ?? null, passed !== undefined, Date.now());
      if (leftReason) {
        // 이어 가지 않습니다. 실패로 끝내거나(창을 닫음) 다른 창에 맡깁니다 — 아래 '창을 닫았다 다시 열었다'
        setTicket((current) => current ?? row?.ticket ?? null);
        setResume({ ...freshResume(row?.clientSessionId ?? null), left: leftReason });
        return;
      }
      if (!row) {
        // 준비 화면을 거쳤는데 행이 없으면(그사이 사이트 데이터를 지웠다) 그 값으로 이어 씁니다 —
        // POST /takes 의 멱등 키가 그 값이라 바꾸면 안 됩니다
        setTicket((current) => current ?? null);
        setResume(freshResume(passed ?? (await startSession(takeId))));
        return;
      }

      const id = row.clientSessionId;
      // 새로고침 — 장치 점검이 세션에 남긴 값이 있습니다. 라우터 state 에 있으면 그게 먼저입니다
      setTicket((current) => current ?? row.ticket ?? null);
      // 메모리 스토어는 새로고침하면 비어 있습니다. 시선 기준·장치를 되살립니다
      if (row.prepare) restorePrepare(row.prepare);

      // 행은 읽었으니 세션은 잇습니다. 기록을 못 읽으면 슬라이드만 처음부터입니다 —
      // 아래 catch 로 보내면 행까지 버려 기록이 하나도 안 쌓이고 시계도 0 부터 돕니다
      let stored: Resume['slideHistory'] = [];
      let gaze: Awaited<ReturnType<typeof readGazeSamples>> = [];
      try {
        const [changes, samples] = await Promise.all([readSlideChanges(id), readGazeSamples(id)]);
        stored = changes;
        gaze = samples;
      } catch (err) {
        console.error('[rehearsal] 슬라이드·시선 기록을 읽지 못했습니다', err);
      }
      // 새로고침 직전에 넘긴 장은 IndexedDB 에 아직 없을 수 있습니다. pagehide 에 적어 둔 기록과 합칩니다
      // (resume.ts `SavedSlides`). 되살릴 장 · 기록이 덮는 시각도 합친 기록으로 셉니다
      const slideHistory = mergeSlideChanges(stored, takeSlides(takeId, id));
      const slide = lastSlide(slideHistory);
      const recordedUntil = recordedUntilMs({
        gaze,
        slideAtMs: slideHistory.map((c) => c.atMs),
      });
      // 무대보다 먼저 되살립니다. 시작하고 나서 바꾸면 1번 슬라이드가 잠깐 보이고 전환으로 기록됩니다
      if (slide) setSlide(slide.slideNumber);

      setResume({
        sessionId: id,
        fromMs: resumeFromMs(row, readClock(takeId), Date.now(), BEAT_MS, recordedUntil),
        slideAt: slide?.atMs ?? null,
        slideHistory,
        ending:
          row.ending ??
          // ending 을 남기기 전에 끝낸 행입니다. 마지막 하트비트가 가장 가까운 값입니다
          (row.status === 'RUNNING'
            ? null
            : { durationMs: row.elapsedMs, endedAtIso: new Date(row.lastBeatAt).toISOString() }),
        submitted: (row.submittedAt ?? null) !== null,
        left: null,
        replaced: false,
      });
      // IndexedDB 를 못 열면(사생활 보호 모드 · 저장 공간 부족) 여기로 옵니다
    })().catch((err: unknown) => {
      console.error('[rehearsal] 세션을 열지 못했습니다', err);
      setSessionError(
        '연습 기록을 저장할 수 없어요. 새로고침하거나 준비 화면에서 다시 시작해 주세요',
      );
      // 기록은 못 쌓아도 발표는 합니다. 무대를 막으면 이 문구만 남은 빈 화면이 됩니다.
      // 준비 화면이 넘겨준 값은 그대로 씁니다 — POST /takes 의 멱등 키라, 그래야 끝내기를 눌렀을 때
      // 재시도 화면으로 가서 같은 값으로 다시 보낼 수 있습니다.
      // 준비 화면을 거치지 않았으면(다른 창에서 엶) 이어 가지 않습니다 (`leftWindow`)
      setTicket((current) => current ?? null);
      setResume(
        passed !== undefined ? freshResume(passed) : { ...freshResume(null), left: 'CLOSED' },
      );
    });
  }, [takeId, location.state, restorePrepare, setSlide]);

  // 이미 끝낸 Take 입니다 (끝낸 뒤 새로고침 · 뒤로 가기). 무대를 다시 열면 같은 Take 를
  // 한 번 더 하게 됩니다. 서버에서 Take 상태를 읽을 길이 없어서 세션 행의 표시로 압니다
  useEffect(() => {
    if (submitted) navigate(`/takes/${takeId}/processing`, { replace: true });
  }, [submitted, takeId, navigate]);

  // 화면을 떠날 때 다음 Take를 위해 무대 상태를 비웁니다
  useEffect(() => resetStore, [resetStore]);

  // 카메라는 준비 화면 CTA를 누른 직후라 바로 열립니다 (같은 문서 = 조작이 살아 있음).
  // 점검에서 쓴 장치를 먼저 열고, 카메라를 못 열면 마이크만 엽니다 (`openDevices.ts`)
  //
  // 세션 행을 읽은 뒤에 엽니다 — 새로고침이면 점검한 장치를 거기서 되살립니다.
  // 종료 중에 새로고침했으면 열지 않습니다. 남은 일은 기록을 보내는 것뿐입니다
  const openDevices = useCallback(() => {
    setOpening(true);
    // 카메라와 마이크를 못 연 이유는 훅이 각각 올립니다 —
    // 마이크까지 못 열면 무대는 시작하지 않습니다 (`startGate`). 여기로 오는 건 예상 밖의 오류뿐이라
    // 버리지 않고 남깁니다
    openStageDevices(devices, { request, requestMic })
      .catch((err: unknown) => {
        console.error('[rehearsal] 카메라·마이크를 여는 중 예상 밖의 오류', err);
      })
      .finally(() => setOpening(false));
  }, [request, requestMic, devices]);
  const askedRef = useRef(false);
  useEffect(() => {
    if (askedRef.current || !resume || resume.ending || resume.submitted || resume.left) return;
    askedRef.current = true;
    openDevices();
  }, [openDevices, resume]);

  /** 시작하지 못한 무대의 '다시 시도'. 장치를 못 열었으면 다시 열고, 연결이 안 됐으면 다시 붙습니다 */
  const retryStart = () => {
    if (gate.kind === 'NO_MIC') {
      openDevices();
      return;
    }
    setConnectSlow(false);
    setConnectAttempt((n) => n + 1);
  };

  // ── 창을 닫았다 다시 열었다 ───────────────────────────────────────
  // 창을 닫은 Take 는 이어 가지 않습니다. 서버에 실패로 끝내 달라고 하고 실패 화면을 보여 줍니다.
  // 무대가 열려 있다 닫힌 경우(닫은 탭을 늦게 복원 · 끝내기가 409)에는 카메라부터 놓습니다.
  // 다른 창이 발표 중이면(서버 409) 행을 건드리지 않습니다 — 그 창이 같은 세션 행을 씁니다
  const closingRef = useRef(false);
  useEffect(() => {
    if (left !== 'CLOSED' || closingRef.current) return;
    closingRef.current = true;
    stopCamera();

    (async () => {
      // 연습 정보가 없으면(이 브라우저에 기록이 없음) 서버에 알릴 주소를 모릅니다. 타이머가 정리합니다
      let outcome: AbandonOutcome = 'CLOSED';
      if (ticket) {
        try {
          outcome = abandonOutcome((await abandonTake()).status, null);
        } catch (e) {
          outcome = abandonOutcome(null, e instanceof ApiFailure ? e.code : null);
          if (outcome === 'CLOSED')
            console.error('[rehearsal] 창을 닫은 Take 를 끝내지 못했습니다', e);
        }
      }
      if (outcome === 'PROCESSING') {
        navigate(`/takes/${takeId}/processing`, { replace: true });
        return;
      }
      if (outcome === 'OTHER_WINDOW') {
        setResume((current) => current && { ...current, left: 'OTHER_WINDOW' });
        return;
      }
      // 무대는 다시 열리지 않습니다. 시선 기준은 얼굴 측정값이라 버립니다
      forgetZoneRef();
      clearClock(takeId);
      clearMetrics(takeId);
      clearSlides(takeId);
      if (sessionId) {
        await endSession(sessionId, 'ABORTED').catch((err: unknown) =>
          noteWriteFailure(sessionId, 'ending', err),
        );
      }
    })().catch((e: unknown) => {
      console.error('[rehearsal] 창을 닫은 Take 정리 실패', e);
    });
  }, [left, ticket, takeId, sessionId, stopCamera, abandonTake, navigate]);

  // ── 제외 사유 배선 ───────────────────────────────────────────────
  // 한 번 정해지면 되돌리지 않습니다. 발표 도중 엔진이 죽었다면 그 Take의
  // 시선 숫자는 앞뒤가 다른 조건에서 나온 것이라 믿을 수 없습니다.
  // 카메라가 끊긴 것은 제외하지 않고 기록만 멈춥니다 — 빈 시간은 서버가 측정 못 함으로
  // 채우므로 끊기기 전 기록을 살립니다 (`lib/gazeExclusion.ts`)
  const excludeGaze = useCallback(
    (id: string, reason: GazeExcludedReason) => {
      noteExclusion(reason);
      markGazeExcluded(id, reason).catch((err: unknown) =>
        noteWriteFailure(id, 'gazeExcluded', err),
      );
    },
    [noteExclusion],
  );

  useEffect(() => {
    if (opening || !sessionId || !cameraError) return;
    const reason = exclusionForDeviceError(cameraError);
    if (reason) excludeGaze(sessionId, reason);
  }, [opening, sessionId, cameraError, excludeGaze]);

  useEffect(() => {
    if (!sessionId || !gazeError) return;
    const reason = exclusionForGazeError(gazeError);
    if (reason) excludeGaze(sessionId, reason);
  }, [sessionId, gazeError, excludeGaze]);

  // 엔진 버전은 종료 시점에 영구 고정됩니다 — 받는 즉시 기록해 둡니다
  useEffect(() => {
    if (!sessionId || !engineVersion) return;
    setEngineVersion(sessionId, engineVersion).catch((err: unknown) =>
      noteWriteFailure(sessionId, 'engineVersion', err),
    );
  }, [sessionId, engineVersion]);

  useEffect(() => {
    if (!sessionId || !perf) return;
    setGazePerf(sessionId, perf.avgFps, perf.droppedFrames).catch((err: unknown) =>
      noteWriteFailure(sessionId, 'gazePerf', err),
    );
  }, [sessionId, perf]);

  // 하트비트 — 벽시계로 찍습니다. 탭이 죽으면 이 값이 마지막 흔적이 되고,
  // 다음에 앱을 열었을 때 "진행 중이던 Take가 있다"를 이걸로 압니다
  useEffect(() => {
    if (!running || !sessionId) return;
    // 여기의 버리기는 의도한 것입니다 — 이 값은 매 틱 덮어써지므로
    // 한 번 실패해도 다음 틱이 같은 자리를 채웁니다. 잃는 것이 없습니다.
    const id = window.setInterval(
      () => beat(sessionId, elapsedMs()).catch(() => undefined),
      BEAT_MS,
    );
    return () => window.clearInterval(id);
  }, [running, sessionId, elapsedMs]);

  // 새로고침하면 이 값에서 시계를 다시 돌립니다. 하트비트는 5초마다라 그만큼 어긋나고,
  // 가려진 탭에서는 브라우저가 더 늦춥니다. 탭이 닫히는 순간에 동기로 적어 둡니다 (resume.ts)
  //
  // 서버로 못 보낸 `metrics` 도 같은 순간에 적습니다 — 시선을 기다리느라 아직 안 만든 마지막 몇 초와
  // 끊겨 있어 소켓이 들고 있던 것. 메모리에만 있어 새로고침하면 사라지고, 새 페이지는 아래 시계부터
  // 다시 보내므로 그 구간을 보낼 길이 없습니다. 같은 `now` 로 메워야 새 시계와 빈틈 · 겹침 없이 이어집니다
  useEffect(() => {
    if (!running || !sessionId) return;
    const save = () => {
      const now = elapsedMs();
      saveClock(takeId, { clientSessionId: sessionId, elapsedMs: now });
      // 끊겨 소켓에만 있던 장 · IndexedDB 쓰기가 끝나지 않은 장까지 이 페이지가 보낸 장 전부입니다
      saveSlides(takeId, {
        clientSessionId: sessionId,
        slides: slideLog().map((s) => ({ atMs: s.t_ms, slideNumber: s.slide_number })),
      });
      saveMetrics(takeId, {
        clientSessionId: sessionId,
        messages: handOffMetrics(flushMetricsForHide(now)),
      });
    };
    window.addEventListener('pagehide', save);
    return () => window.removeEventListener('pagehide', save);
  }, [running, sessionId, takeId, elapsedMs, handOffMetrics, flushMetricsForHide, slideLog]);

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

  /**
   * 지금 슬라이드에 매핑된 대본만 보여 줍니다. 대본은 AI 가 이미 슬라이드별로 나눠 주므로
   * n번 슬라이드의 대본이 곧 대본 매핑 n번입니다. 매핑이 없는 장은 빈 문자열입니다.
   */
  const currentSlideScript = materials.scriptBySlide.get(slideNumber);
  const currentScript = currentSlideScript?.content ?? '';
  const currentKeywords = currentSlideScript?.keywords ?? NO_KEYWORDS;
  const currentHighlights = currentSlideScript?.highlights ?? NO_HIGHLIGHTS;
  const nextSlideNumber = Math.min(slideNumber + 1, pageCount);

  // ── 종료 ─────────────────────────────────────────────────────────
  const endable = canEnd(pageCount, slideNumber);
  const { confirming, ask: askEnd } = useEndConfirm(slideNumber);
  const finish = async () => {
    if (!running || !sessionId || !ticket) return;

    // 시간을 먼저 붙잡습니다. 아래 await들이 도는 동안에도 시계는 갑니다.
    // ★ 끝난 시각도 여기서 찍습니다 — STT 정리는 최대 3초까지 걸리는데,
    //   그 시간을 endedAt에 얹으면 endedAt - startedAt이 durationMs와 어긋납니다
    // oxlint-disable-next-line react/purity -- 렌더에서는 호출하지 않고 클릭·서버 자동 종료 이벤트에서만 종료 시각을 고정합니다.
    const ended: Ending = { durationMs: elapsedMs(), endedAtIso: new Date().toISOString() };

    setPhase('ENDING');

    // 1초가 안 찬 마지막 시선 조각을 발표 길이까지로 닫아 저장합니다 (최대 0.5초, 실패하지 않습니다)
    await flushGaze(ended.durationMs);
    // 그 조각까지 안 보낸 시간을 발표 길이까지 `metrics` 로 보냅니다. 아래 `stop` 보다 먼저 나갑니다
    // ★ 서버 자동 종료(`onAutoEnd`)로 왔으면 서버는 Deepgram 정리 뒤 이 조각을 **2초만** 기다리고
    //   연결을 닫습니다 (BE `FINAL_METRICS_WAIT_SEC`). 닫힌 뒤 보내면 버려집니다 — 여기까지 오는
    //   작업(`flushGaze` 최대 0.5초)을 늘리지 마세요. 오래 걸리는 일은 `finishMetrics` 뒤에 둡니다
    finishMetrics(ended.durationMs);

    // ★ 종료 중에 새로고침해도 무대로 돌아가지 않고 이 값으로 종료를 이어가도록 먼저 적습니다.
    //   못 적어도 막지 않습니다 — 그때 새로고침하면 발표 중으로 돌아갈 뿐입니다
    await markEnding(sessionId, ended).catch((err: unknown) =>
      noteWriteFailure(sessionId, 'ending', err),
    );

    // 서버가 남은 오디오를 Deepgram에 흘리고 `closed`를 줄 때까지 기다립니다.
    // 보통 1초, 최대 3초입니다. 그 사이 버튼은 '정리하는 중…'을 보여 줍니다
    // ★ 이 WS `stop` 이 서버에 Take 가 끝났다고 알리는 신호입니다 (`architecture.md` 1-3).
    //   3초에서 끊겨도 서버는 마지막 전사까지 저장합니다
    await stopStt();
    await submit(sessionId, ended);
  };

  /**
   * Take 의 시작 · 종료 시각을 BE 에 적고 처리 화면으로 갑니다. 끝내기 버튼과 **종료 중 새로고침**이 같이 씁니다.
   *
   * 보내는 일은 재시도 화면(P15)과 같이 쓰는 `sendEnding` 이 합니다.
   */
  const submit = async (id: string, ending: Ending) => {
    if (!ticket) return;

    // 무대는 다시 열리지 않으니 시선 기준은 더 쓸 데가 없습니다. 얼굴 측정값이 들어 있어 버립니다
    forgetZoneRef();

    // ★ 여기서부터 끝까지 한 try 입니다. 중간이 실패해도 **무대를 되살리면 안 됩니다** —
    //   phase 가 RUNNING 으로 돌아가면 clock.start() 가 t0 를 다시 잡아 durationMs 가
    //   어긋나고, useSlideDeck 이 0ms 행을 덮어씁니다 (rehearsalStore 주석 참고).
    //   기록은 IndexedDB 에 그대로 있으므로 재시도 화면으로 보냅니다.
    try {
      await sendEnding(takeId, id, ending, endTake.mutateAsync);
      // 기록을 쌓지 않고 바꿉니다. 뒤로 가기로 리허설 화면에 돌아오지 않게 합니다
      navigate(`/takes/${takeId}/processing`, { replace: true });
    } catch (e) {
      // 서버가 이미 끝낸 Take 입니다 — 창을 닫아 끝났는데(닫은 탭을 늦게 복원) 끝내기를 눌렀습니다.
      // 다시 보내도 같아서 재시도 화면이 아니라 실패 화면으로 갑니다
      if (e instanceof ApiFailure && e.code === 'TAKE_ENDED') {
        leaveClosed();
        return;
      }
      // 서버에서 시작하지 않은 Take 입니다 — 남은 기록이 없어 다시 보내도 같습니다
      if (e instanceof ApiFailure && e.code === 'TAKE_NOT_STARTED') {
        setNotStarted(true);
        return;
      }
      // 기록은 브라우저에 그대로 있습니다. 여기서 잃는 것은 없고,
      // 재시도 화면이 같은 clientSessionId 의 세션으로 다시 보냅니다
      console.error('[rehearsal] 종료 처리 실패', e);
      setEndError(toMessage(e));
      navigate(`/takes/${takeId}/retry`, { state: { clientSessionId: id } });
    }
  };

  // 자동 종료(onAutoEnd)가 최신 finish 를 부르게 합니다. finish 가 쓰는 submit 보다 뒤에 둡니다
  useEffect(() => {
    finishRef.current = finish;
  });

  // 종료 중에 새로고침했습니다. 무대는 열지 않고 끝내기를 누른 순간 고정한 값으로 종료를 이어갑니다.
  // 종료 시각은 같은 값을 다시 적을 뿐이라 새로고침 전에 이미 갔어도 다시 보내도 됩니다.
  // 의존성 배열을 두지 않습니다 — submit 이 매 렌더 새로 만들어지는 함수라 넣으면 매번 돌고,
  // 빼면 오래된 submit 을 잡습니다. 한 번만 도는 것은 ref 가 지킵니다
  const resumedEndingRef = useRef(false);
  useEffect(() => {
    if (resumedEndingRef.current || !ending || !sessionId || !ticket || submitted) return;
    resumedEndingRef.current = true;

    setPhase('ENDING');
    // 시계는 돌지 않으므로 머리줄에 발표 길이를 직접 적습니다
    if (elapsedRef.current) elapsedRef.current.textContent = formatDuration(ending.durationMs);
    (async () => {
      // 새로고침 전에 `stop` 이 서버에 못 닿았을 수 있습니다. 그러면 서버는 끊긴 연결을 30초
      // 기다린 뒤에야 마지막 전사를 정리합니다. `stop` 이 Take 종료 신호이기도 해서
      // 붙어서 stop 만 보냅니다 (최대 3초). 이미 닿았으면 서버가 열었다 바로 닫습니다
      await stopTakeStream(takeId);
      await submit(sessionId, ending);
    })().catch((e: unknown) => {
      console.error('[rehearsal] 종료 처리 실패', e);
      setEndError(toMessage(e));
    });
  });

  if (left)
    return (
      <LeftWindowNotice
        left={left}
        replaced={resume?.replaced ?? false}
        pitchId={ticket?.pitchId ?? null}
      />
    );
  if (notStarted || (gate.kind === 'FAILED' && gate.reason === 'ENDED')) {
    return <NotStartedNotice pitchId={ticket?.pitchId ?? null} />;
  }

  // 시선이 기록되지 않을 때만 발밑에 한 줄 띄웁니다(`gazeNote`). 발표 중에 고칠 수 없는 문제도 남깁니다 —
  // 코치 상태 표시는 코칭 모드에서 소켓이 붙어 있을 때만 와서, 실전 모드 · 끊긴 동안에는 아무 표시가 없습니다
  // 장치를 모두 연 뒤 발표 중에만 알립니다. 중간 오류나 종료 중 엔진 상태로 안내하지 않습니다
  const gazeLine =
    running && !opening
      ? gazeNote({
          declined: cameraError === 'PERMISSION_DENIED',
          noCamera: stream !== null && !cameraOpened,
          cameraLost:
            (cameraError !== null && cameraError !== 'PERMISSION_DENIED') ||
            gazeError === 'CAMERA_LOST',
          missingCalibration,
          error: gazeError,
          ready: gazeReady,
        })
      : null;

  return (
    <div className="rehearsal-page">
      <div className="rehearsal-content">
        <h1 className="sr-only">발표 연습 · Take {ticket?.takeNumber ?? ''}</h1>

        <div className="rehearsal-frame">
          {/* ★ data-gaze 한 줄이 테두리의 전부입니다. 워커의 1초 판정이 여기 도착합니다 */}
          <div
            className="stage stage-live"
            ref={stageRef}
            data-script-mode={scriptMode}
            data-gaze="UNKNOWN"
            data-mode={mode}
          >
            {stageReady && gate.kind !== 'STARTED' && (
              <StartGateNotice gate={gate} pitchId={ticket?.pitchId ?? null} onRetry={retryStart} />
            )}
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
                SLIDE {slideNumber} / {pageCount || '—'}
              </span>
              <button
                type="button"
                className="end"
                disabled={!running || phase !== 'RUNNING' || !endable}
                aria-describedby={endable ? undefined : 'end-hint'}
                onClick={() => {
                  // 되돌릴 수 없어서 한 번 더 묻습니다. 모달을 띄우지 않는 이유는
                  // 발표 중에 화면을 덮으면 남은 시간을 못 보기 때문입니다
                  if (!confirming) {
                    askEnd();
                    return;
                  }
                  // 복구는 finish() 안에서 끝납니다 (재시도 화면으로 이동).
                  // try 밖에서 던질 것이 없지만, 새어 나오면 삼키지 않고 남깁니다
                  finish().catch((e: unknown) => {
                    console.error('[rehearsal] 종료 처리 실패', e);
                    setEndError(toMessage(e));
                  });
                }}
              >
                {endButtonText(phase === 'ENDING', confirming)}
              </button>
            </header>

            <div className="viewport">
              <div className="slide">
                <div className="slide-content">
                  {/* PDF 한 장을 상자에 맞춰 그립니다. 못 열었으면 자리표시 — 깨진 화면보다 낫습니다 */}
                  {materials.doc ? (
                    <PdfPage
                      doc={materials.doc}
                      pageNumber={slideNumber}
                      fit="contain"
                      className="h-full min-h-0 w-full min-w-0 overflow-hidden"
                    />
                  ) : (
                    <span className="placeholder">SLIDE {slideNumber} · 16:9</span>
                  )}
                  {!materials.doc && (
                    <span className="note">
                      {materials.presentationFailed
                        ? '발표자료를 불러오지 못했어요'
                        : '발표자료를 여는 중…'}
                    </span>
                  )}
                </div>
              </div>

              <div className="rail">
                <section className="camera">
                  <div className="frame">
                    <video ref={videoRef} muted playsInline />
                    {!(live && cameraOpened) && <div className="guide" />}
                    <span className="tag">CAMERA 4:3</span>
                  </div>
                </section>

                <section className="mic">
                  <LevelBar variant="segments" meterRef={meterRef} />
                </section>

                <section className="next" hidden={slideNumber >= pageCount}>
                  <div className="thumb">
                    {materials.doc && nextSlideNumber > 0 ? (
                      <PdfPage
                        doc={materials.doc}
                        pageNumber={nextSlideNumber}
                        fit="contain"
                        className="h-full min-h-0 w-full min-w-0 overflow-hidden"
                      />
                    ) : (
                      `SLIDE ${nextSlideNumber} · 16:9`
                    )}
                  </div>
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

            <CoachStrip
              message={coach?.text ?? null}
              exam={mode === 'EXAM'}
              praise={coach?.instruction === 'CONTINUE'}
            >
              <CoachIndicators indicators={indicators} stale={indicatorsStale} />
            </CoachStrip>

            <ScriptPane
              mode={scriptMode}
              slideNumber={slideNumber}
              text={currentScript}
              keywords={currentKeywords}
              highlights={currentHighlights}
            />

            <div className="stage-foot">
              <div className="stage-notes">
                {/* 끝내기 실패는 꼭 봐야 해서 스크롤되는 상태 칸 밖, 조작 안내 자리에 띄웁니다 */}
                {endError ? (
                  <span className="end-error" role="alert">
                    {endError}
                  </span>
                ) : (
                  <span className="key-help">
                    ← → 슬라이드 이동 · Space 다음 장{scriptMode !== 'OFF' && ' · ↑ ↓ 대본 스크롤'}
                  </span>
                )}
                {/* 알림 영역은 안쪽 문구가 각자 잡습니다 — 여기도 status 면 낭독기가 두 번 읽습니다 */}
                <div className="stage-status">
                  {/* 기록이 멈추면 경고(stage.css) — 화면 낭독기에도 바로 알립니다. 준비 중은 조용히 둡니다.
                    key 로 요소를 갈아 끼웁니다 — 있던 status 의 role 만 alert 로 바꾸면 낭독기가 놓칩니다 */}
                  {gazeLine && (
                    <span
                      key={gazeLine.alert ? 'alert' : 'status'}
                      className="gaze"
                      data-alert={gazeLine.alert}
                      role={gazeLine.alert ? 'alert' : 'status'}
                    >
                      {gazeLine.text}
                    </span>
                  )}
                  {sttNote && (
                    <span className="stt" data-alert={sttAlert}>
                      {sttNote}
                    </span>
                  )}
                  {/* 실시간 전사는 띄우지 않습니다 — 자기 말이 글로 따라붙으면 그걸 읽게 됩니다.
                  전사는 그대로 서버에 쌓이니 리포트에서는 차이가 없습니다 */}
                  {audioState !== null && audioState !== 'running' && (
                    <span>소리가 흐르지 않습니다 — 화면을 한 번 클릭해 주세요</span>
                  )}
                  {ticket === null && (
                    <span>연습 정보를 찾을 수 없어요. 장치 점검부터 다시 시작해 주세요.</span>
                  )}
                  {sessionError && <span>{sessionError}</span>}
                </div>

                {/* 비활성 버튼은 초점을 못 받고 title 도 잘 안 보여서 이유를 글자로 둡니다 */}
                {!endable && (
                  <span id="end-hint" className="end-hint">
                    마지막 장에서 끝낼 수 있어요
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 무대를 열지 않는 이유의 제목과 치치의 말 */
function leftWindowText(left: LeftWindow, replaced: boolean): { title: string; line: string } {
  if (left === 'OTHER_WINDOW')
    return {
      title: '다른 창에서 연습 중',
      line: '이 연습은 다른 창에서 하고 있어. 그 창으로 돌아가 줘!',
    };
  if (replaced)
    return {
      title: '연습 중단',
      line: '다른 곳에서 새 연습을 시작해서 이번 연습은 끝났어. 그쪽에서 이어서 연습해 줘!',
    };
  return { title: '연습 중단', line: '창이 닫혀서 이번 연습은 중단됐어. 새로 시작해 보자!' };
}

/**
 * 무대를 열지 않는 이유를 알립니다. 창을 닫은 Take 는 이어 가지 않으니 새 Take 로 보냅니다.
 * 다른 창이 발표 중이면 그 창으로 돌아가게 합니다 — 여기서는 아무것도 바꾸지 않았습니다.
 * 다른 곳에서 새 Take 를 시작해 끝났으면 그쪽이 이번 연습이라 여기서 새 Take 를 권하지 않습니다.
 */
function LeftWindowNotice({
  left,
  replaced,
  pitchId,
}: {
  left: LeftWindow;
  replaced: boolean;
  pitchId: string | null;
}) {
  const closed = left === 'CLOSED';
  const { title, line } = leftWindowText(left, replaced);
  return (
    <InfoPage title={title}>
      <Card>
        <Mascot who="chichi">{line}</Mascot>
        <div className="flex flex-wrap gap-3">
          {closed && !replaced && pitchId && (
            <ButtonLink to={`/pitch/${pitchId}/device-check`}>새 Take 준비</ButtonLink>
          )}
          <ButtonLink to="/" variant="secondary">
            내 피치로
          </ButtonLink>
        </div>
      </Card>
    </InfoPage>
  );
}

/**
 * 시작하기 전에 끝난 Take 입니다. 서버 연결이 끝내 붙지 않아 시작하지 않은 Take 로 정리됐습니다
 * (만든 지 10분 · 같은 발표에서 새 Take 를 만듦). 이 Take 는 다시 열 수 없어 새로 준비합니다.
 */
function NotStartedNotice({ pitchId }: { pitchId: string | null }) {
  return (
    <InfoPage title="연습 시작 안 됨">
      <Card>
        <Mascot who="chichi">이 연습은 시작하기 전에 끝났어. 장치 점검부터 다시 해 보자!</Mascot>
        <div className="flex flex-wrap gap-3">
          {pitchId && <ButtonLink to={`/pitch/${pitchId}/device-check`}>새 Take 준비</ButtonLink>}
          <ButtonLink to="/" variant="secondary">
            내 피치로
          </ButtonLink>
        </div>
      </Card>
    </InfoPage>
  );
}

/**
 * 발표를 시작하기 전에 무대 위에 띄우는 안내. 시계 · 기록 · 끝내기는 아직 멈춰 있습니다.
 * 이어 가는 Take(새로고침)에서는 장치를 못 열었을 때만 뜹니다.
 */
function StartGateNotice({
  gate,
  pitchId,
  onRetry,
}: {
  gate: Exclude<StartGate, { kind: 'STARTED' }>;
  pitchId: string | null;
  onRetry: () => void;
}) {
  const text = startGateText(gate);
  const stuck =
    gate.kind === 'NO_MIC' || gate.kind === 'FAILED' || (gate.kind === 'CONNECTING' && gate.slow);
  return (
    <div className="start-gate" role="status" data-stuck={stuck || undefined}>
      <div className="box">
        <p>{text}</p>
        {stuck && (
          <div className="actions">
            <Button onClick={onRetry}>다시 시도</Button>
            {pitchId && (
              <ButtonLink variant="secondary" to={`/pitch/${pitchId}/device-check`}>
                장치 점검으로
              </ButtonLink>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function startGateText(gate: Exclude<StartGate, { kind: 'STARTED' }>): string {
  switch (gate.kind) {
    case 'OPENING':
      return '카메라와 마이크를 여는 중이에요';
    case 'NO_MIC':
      return `${gate.error ? `${MIC_ERROR_MESSAGE[gate.error]} ` : ''}마이크가 있어야 발표를 시작할 수 있어요.`;
    case 'CONNECTING':
      return gate.slow
        ? '말하기 분석 서버에 연결하지 못했어요. 연결되면 바로 시작해요.'
        : '말하기 분석 서버에 연결하는 중이에요';
    case 'FAILED':
      return gate.reason === 'BUSY'
        ? '다른 기기나 창에서 연습이 진행 중이에요. 그 연습을 끝낸 뒤 다시 시도해 주세요.'
        : '말하기 분석 서버에 연결하지 못했어요. 다시 시도해 주세요.';
  }
}

function endButtonText(ending: boolean, confirming: boolean): string {
  if (ending) return '정리하는 중…';
  if (confirming) return '정말 끝낼까요?';
  return '발표 끝내기';
}

/**
 * AI 코치의 상태 표시. 받은 영역만, 늘 같은 순서로 둡니다. 고칠 것이 있는 영역만 색을 입힙니다 —
 * 여섯 칸이 모두 칠해져 있으면 어디를 봐야 할지 모릅니다.
 */
function CoachIndicators({ indicators, stale }: { indicators: Indicators; stale: boolean }) {
  const views = indicatorViews(indicators);
  if (views.length === 0) return null;
  return (
    <ul
      className="indicators"
      data-stale={stale}
      aria-label={stale ? '코치 상태 표시 (연결이 끊겨 지난 판정)' : '코치 상태 표시'}
      title={stale ? '연결이 끊겨 지난 판정이에요' : undefined}
    >
      {views.map((v) => (
        <li key={v.area} data-tone={v.tone}>
          {v.label} <b>{v.value}</b>
        </li>
      ))}
    </ul>
  );
}
