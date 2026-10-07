import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { LevelBar } from '../media/LevelBar';
import { useCameraStream } from '../media/useCameraStream';
import { useMicLevel } from '../media/useMicLevel';
import { useVideoStream } from '../media/useVideoStream';
import { usePrepareStore } from '../prepare/prepareStore';
import { useCompleteTake } from '@/shared/api/take';
import { PdfPage } from '@/shared/ui/PdfPage';
import { buildGazePayload } from '../lib/gazePayload';
import { stopTakeStream } from '../lib/sttSocket';
import {
  beat,
  endSession,
  findSessionByTakeId,
  getSession,
  markEnding,
  markSubmitted,
  markGazeExcluded,
  readCoachLog,
  readGazeDecisions,
  readSlideChanges,
  setEngineVersion,
  setGazePerf,
  startSession,
} from '../lib/db';
import { clearWriteFailures, noteWriteFailure, readWriteFailures } from '../lib/writeFailures';
import { toMessage } from '@/shared/api/errorMessage';
import { ScreenLabel } from '@/shared/ui/ScreenLabel';
import { TemporalVoter } from '@/workers/temporalVoter';
import { formatDuration } from '@/shared/lib/clock';
import type { CompleteRequest, GazeExcludedReason, Ms, RehearsalTicket } from '@/types/api';
import { ScriptPane } from './ScriptPane';
import { useCoach } from './useCoach';
import { useLiveGaze } from './useLiveGaze';
import { useRehearsalMaterials } from './useRehearsalMaterials';
import { useRehearsalStore } from './rehearsalStore';
import {
  clearClock,
  coachHistory,
  lastSlide,
  readClock,
  resumeFromMs,
  saveClock,
  type CoachHistory,
} from './resume';
import { useSlideDeck } from './useSlideDeck';
import { useStageClock } from './useStageClock';
import { useSttStream } from './useSttStream';
import type { TextRange } from '../lib/scriptMarks';
import './stage.css';

/** 하트비트 주기. 탭이 죽으면 이 값이 멈춘 시각이 마지막 흔적입니다 */
const BEAT_MS = 5_000;

/** 대본이 없는 장의 기본값. 매 렌더 새 배열을 만들면 대본 칸의 강조 계산이 매번 다시 돕니다 */
const NO_KEYWORDS: string[] = [];
const NO_HIGHLIGHTS: TextRange[] = [];

interface Ending {
  durationMs: Ms;
  endedAtIso: string;
}

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
  coach: CoachHistory | null;
  /** 끝내기를 누른 뒤에 새로고침했으면 그때 고정한 값. 무대를 열지 않고 종료를 이어갑니다 */
  ending: Ending | null;
  /** `/complete` 까지 보낸 Take. 무대를 열지 않고 처리 화면으로 보냅니다 */
  submitted: boolean;
}

const freshResume = (sessionId: string | null): Resume => ({
  sessionId,
  fromMs: 0,
  slideAt: null,
  coach: null,
  ending: null,
  submitted: false,
});

/**
 * 07 발표 연습 (P5 · P5x) — 리허설 무대.
 *
 * ── 이 화면이 지키는 것 ─────────────────────────────────────────────
 *
 * 1. **브라우저가 원본입니다.** 시선 판정·슬라이드 전환·코치 기록이
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
 * ── 2단(서버)은 어디까지 왔나 ───────────────────────────────────────
 * 마이크 PCM을 WebSocket으로 흘리고 전사를 받는 데까지 붙었습니다(`useSttStream`).
 * 속도·군더더기·대본 일치는 서버가 아직 `metrics`·`coach` 메시지를 안 보내서
 * 코치 줄에는 들어오지 않습니다. 1단이 그것과 무관하게 돈다는 게 이 구조의 요점입니다.
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
  const complete = useCompleteTake(takeId);

  const phase = useRehearsalStore((s) => s.phase);
  const coach = useRehearsalStore((s) => s.coach);
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
   */
  const excludedRef = useRef<GazeExcludedReason | null>(null);
  const noteExclusion = useCallback((reason: GazeExcludedReason) => {
    excludedRef.current ??= reason;
  }, []);
  const [resume, setResume] = useState<Resume | null>(null);
  const sessionId = resume?.sessionId ?? null;
  const ending = resume?.ending ?? null;
  const [confirming, setConfirming] = useState(false);
  const [endError, setEndError] = useState<string | null>(null);
  /**
   * 세션을 못 연 이유. 이게 있으면 이 Take 의 기록(시선·슬라이드·코치)이 하나도 쌓이지 않고
   * 종료 버튼도 아무것도 하지 않습니다 — 조용히 넘어가면 발표를 다 하고 나서야 압니다.
   */
  const [sessionError, setSessionError] = useState<string | null>(null);

  const { stream, error: deviceError, request } = useCameraStream();
  const { videoRef, live } = useVideoStream(stream, 'rehearsal');
  const { meterRef, statsRef, audioState } = useMicLevel(stream);

  const ready = materials.ready && resume !== null;
  /** 이미 `/complete` 까지 보낸 Take 입니다 (끝낸 뒤 새로고침 · 뒤로 가기). 무대를 열지 않습니다 */
  const submitted = resume?.submitted ?? false;
  /** 무대를 열 수 있나. 종료 중에 새로고침했으면 열지 않고 종료만 이어갑니다 */
  const stageReady = ready && ending === null && !submitted;
  const running = stageReady && phase === 'RUNNING';
  const limitSec = ticket?.timeLimitSec ?? 600;
  const scriptMode = ticket?.scriptMode ?? 'HIGHLIGHT';
  const mode = ticket?.mode ?? 'COACHING';
  const pageCount = materials.pageCount;

  const { elapsedRef, limitRef, barRef, fillRef, remainRef, elapsedMs } = useStageClock(
    limitSec,
    running,
    resume?.fromMs ?? 0,
  );
  const {
    ready: gazeReady,
    engineVersion,
    error: gazeError,
    perf,
    bottomRatio,
    missingCalibration,
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
    // 워커가 "카메라 소실"로 읽고 스스로 멈춥니다
    enabled: running && live,
  });
  const { slideNumber, slideStartedAtRef } = useSlideDeck({
    total: pageCount,
    clientSessionId: sessionId,
    elapsedMs,
    enabled: running,
    resumedAt: resume?.slideAt ?? null,
  });
  /**
   * 2단 코치의 입력선. `ENDING`에도 살려 두는 이유는 종료 CTA가 `stop`을 보내고
   * 서버의 `closed`를 기다려야 하기 때문입니다 — 여기서 끊으면 마지막 문장이 사라집니다.
   */
  const {
    transcriptRef,
    note: sttNote,
    alert: sttAlert,
    stop: stopStt,
  } = useSttStream({
    takeId,
    stream,
    enabled: stageReady && (phase === 'RUNNING' || phase === 'ENDING'),
    elapsedMs,
  });

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
    history: resume?.coach ?? null,
  });

  // ── 세션 잇기 ────────────────────────────────────────────────────
  // 준비 화면이 넘겨준 값이 먼저입니다. 없으면(다른 탭에서 이 주소를 열었다) takeId로
  // IndexedDB를 뒤지고, 그래도 없으면 새로 엽니다.
  //
  // ★ 새로고침해도 location.state 는 남습니다. 그래서 막 넘어왔는지 새로고침했는지는
  //   행을 읽어야 압니다 — 두 길 모두 행을 읽고, 이어받을 것(시계·슬라이드·코치·준비 값·
  //   종료 중이었나)을 모은 뒤에 무대를 엽니다.
  const resolvedRef = useRef(false);
  useEffect(() => {
    if (resolvedRef.current || takeId === '') return;
    resolvedRef.current = true;

    const passed = (location.state as { clientSessionId?: string } | null)?.clientSessionId;
    (async () => {
      const row = passed ? await getSession(passed) : await findSessionByTakeId(takeId);
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

      // 행은 읽었으니 세션은 잇습니다. 슬라이드·코치 기록을 못 읽으면 그 둘만 처음부터입니다 —
      // 아래 catch 로 보내면 행까지 버려 기록이 하나도 안 쌓이고 시계도 0 부터 돕니다
      let slide: ReturnType<typeof lastSlide> = null;
      let coach: CoachHistory | null = null;
      try {
        const [changes, coachRows] = await Promise.all([readSlideChanges(id), readCoachLog(id)]);
        slide = lastSlide(changes);
        coach = coachHistory(coachRows);
      } catch (err) {
        console.error('[rehearsal] 슬라이드·코치 기록을 읽지 못했습니다', err);
      }
      // 무대보다 먼저 되살립니다. 시작하고 나서 바꾸면 1번 슬라이드가 잠깐 보이고 전환으로 기록됩니다
      if (slide) setSlide(slide.slideNumber);

      setResume({
        sessionId: id,
        fromMs: resumeFromMs(row, readClock(takeId), Date.now(), BEAT_MS),
        slideAt: slide?.atMs ?? null,
        coach,
        ending:
          row.ending ??
          // ending 을 남기기 전에 끝낸 행입니다. 마지막 하트비트가 가장 가까운 값입니다
          (row.status === 'RUNNING'
            ? null
            : { durationMs: row.elapsedMs, endedAtIso: new Date(row.lastBeatAt).toISOString() }),
        submitted: (row.submittedAt ?? null) !== null,
      });
      // IndexedDB 를 못 열면(사생활 보호 모드 · 저장 공간 부족) 여기로 옵니다
    })().catch((err: unknown) => {
      console.error('[rehearsal] 세션을 열지 못했습니다', err);
      setSessionError(
        '연습 기록을 저장할 수 없어요. 새로고침하거나 준비 화면에서 다시 시작해 주세요',
      );
      // 기록은 못 쌓아도 발표는 합니다. 무대를 막으면 이 문구만 남은 빈 화면이 됩니다.
      // 준비 화면이 넘겨준 값은 그대로 씁니다 — POST /takes 의 멱등 키라, 그래야 끝내기를 눌렀을 때
      // 재시도 화면으로 가서 같은 값으로 다시 보낼 수 있습니다. 없으면 끝내기는 아무것도 하지 않습니다
      setTicket((current) => current ?? null);
      setResume(freshResume(passed ?? null));
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
  // 점검에서 쓴 장치를 그대로 엽니다 — 기본 장치를 열면 USB 마이크로 점검하고
  // 내장 마이크로 녹음하는 일이 생깁니다
  //
  // 세션 행을 읽은 뒤에 엽니다 — 새로고침이면 점검한 장치를 거기서 되살립니다.
  // 종료 중에 새로고침했으면 열지 않습니다. 남은 일은 기록을 보내는 것뿐입니다
  const askedRef = useRef(false);
  useEffect(() => {
    if (askedRef.current || !resume || resume.ending || resume.submitted) return;
    askedRef.current = true;

    (async () => {
      const opened = await request(devices);
      // 고른 장치가 그사이 빠졌으면(USB 분리 등) exact 제약에 걸려 못 엽니다.
      // 발표를 못 여는 것보다 기본 장치로라도 여는 편이 낫습니다
      if (!opened && (devices.videoDeviceId || devices.audioDeviceId)) {
        await request();
      }
      // 장치를 못 연 이유(권한 거부 · 장치 없음)는 request 가 deviceError 로 올립니다 —
      // 시선 제외와 화면 표시는 그쪽이 맡습니다. 여기로 오는 건 예상 밖의 오류뿐이라
      // 버리지 않고 남깁니다
    })().catch((err: unknown) => {
      console.error('[rehearsal] 카메라·마이크를 여는 중 예상 밖의 오류', err);
    });
  }, [request, devices, resume]);

  // ── 제외 사유 배선 ───────────────────────────────────────────────
  // 한 번 정해지면 되돌리지 않습니다. 발표 도중 엔진이 죽었다면 그 Take의
  // 시선 숫자는 앞뒤가 다른 조건에서 나온 것이라 믿을 수 없습니다.
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
    if (!sessionId || !deviceError) return;
    excludeGaze(sessionId, deviceError === 'PERMISSION_DENIED' ? 'USER_DECLINED' : 'CAMERA_LOST');
  }, [sessionId, deviceError, excludeGaze]);

  useEffect(() => {
    if (!sessionId || !gazeError) return;
    excludeGaze(sessionId, gazeError);
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
  useEffect(() => {
    if (!running || !sessionId) return;
    const save = () => saveClock(takeId, { clientSessionId: sessionId, elapsedMs: elapsedMs() });
    window.addEventListener('pagehide', save);
    return () => window.removeEventListener('pagehide', save);
  }, [running, sessionId, takeId, elapsedMs]);

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
  const finish = async () => {
    if (!running || !sessionId || !ticket) return;

    // 시간을 먼저 붙잡습니다. 아래 await들이 도는 동안에도 시계는 갑니다.
    // ★ 끝난 시각도 여기서 찍습니다 — STT 정리는 최대 3초까지 걸리는데,
    //   그 시간을 endedAt에 얹으면 endedAt - startedAt이 durationMs와 어긋납니다
    const ended: Ending = { durationMs: elapsedMs(), endedAtIso: new Date().toISOString() };

    setPhase('ENDING');

    // ★ 종료 중에 새로고침해도 무대로 돌아가지 않고 이 값으로 종료를 이어가도록 먼저 적습니다.
    //   못 적어도 막지 않습니다 — 그때 새로고침하면 발표 중으로 돌아갈 뿐입니다
    await markEnding(sessionId, ended).catch((err: unknown) =>
      noteWriteFailure(sessionId, 'ending', err),
    );

    // 서버가 남은 오디오를 Deepgram에 흘리고 `closed`를 줄 때까지 기다립니다.
    // 보통 1초, 최대 3초입니다. 그 사이 버튼은 '정리하는 중…'을 보여 줍니다
    // ★ 3초에서 끊겨도 서버는 마지막 전사까지 저장합니다. 그래서 아래 `/complete`가
    //   그 저장보다 먼저 도착할 수 있습니다 — 분석이 저장을 기다리는 것은 BE 몫입니다
    await stopStt();
    await submit(sessionId, ended);
  };

  /**
   * 기록을 모아 `/complete` 로 보냅니다. 끝내기 버튼과 **종료 중 새로고침**이 같이 씁니다.
   *
   * 새로고침으로 들어왔으면 메모리에 남은 것이 없습니다. 그래서 행(IndexedDB)이 먼저이고
   * 메모리 값은 행을 못 적었을 때만 받습니다.
   */
  const submit = async (id: string, { durationMs, endedAtIso }: Ending) => {
    if (!ticket) return;

    // ★ 여기서부터 끝까지 한 try 입니다. 중간이 실패해도 **무대를 되살리면 안 됩니다** —
    //   phase 가 RUNNING 으로 돌아가면 clock.start() 가 t0 를 다시 잡아 durationMs 가
    //   어긋나고, useSlideDeck 이 0ms 행을 덮어씁니다 (rehearsalStore 주석 참고).
    //   기록은 IndexedDB 에 그대로 있으므로 재시도 화면으로 보냅니다.
    try {
      await endSession(id);

      // ── 대조 ────────────────────────────────────────────────────────
      // 발표는 한 번뿐이라 여기가 마지막 확인입니다. 장부에 쌓인 실패를 봅니다.
      // ★ 지금은 알리는 곳이 콘솔뿐입니다.
      const failures = readWriteFailures(id);
      if (Object.keys(failures).length > 0) {
        console.error('[rehearsal] 기록이 불완전합니다', { 실패: failures });
      }

      const row = await getSession(id);
      const decisions = await readGazeDecisions(id);
      const changes = await readSlideChanges(id);
      const coachRows = await readCoachLog(id);

      const gazePayload = buildGazePayload({
        decisions,
        durationMs,
        // DB 쓰기가 실패했어도 값 자체는 메모리에 있습니다. 행을 못 읽었다고
        // ENGINE_VERSION_UNAVAILABLE 로 보내면 없는 사실을 만들어 내는 셈입니다
        engineVersion: row?.engineVersion ?? engineVersion,
        decisionIntervalMs: TemporalVoter.INTERVAL_MS,
        // 행이 우선이고, 못 적혔으면 ref 가 받습니다. 제외를 놓치는 쪽이
        // 잘못 제외하는 쪽보다 나쁩니다 — 틀린 숫자가 정상인 척 실리니까요
        excludedReason: row?.gazeExcluded ? row.gazeExcludedReason : excludedRef.current,
        calibration: row?.prepare?.calibration ?? calibration,
      });

      const body: CompleteRequest = {
        clientSessionId: id,
        startedAt: row?.startedAtIso ?? new Date(Date.now() - durationMs).toISOString(),
        endedAt: endedAtIso,
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
        clientPerf: {
          avgGazeFps: row?.gazeAvgFps ?? perf?.avgFps ?? 0,
          droppedFrames: row?.gazeDroppedFrames ?? perf?.droppedFrames ?? 0,
          // LIGHT 강등 임계값은 I-03이 정해져야 만들 수 있습니다
          degradedToLightAtMs: null,
        },
      };

      await complete.mutateAsync(body);
      // 보냈다는 표시. 이 주소로 다시 들어오면(새로고침 · 뒤로 가기) 무대를 열지 않습니다.
      // 못 적으면 다시 들어왔을 때 종료를 한 번 더 이어가는데, `/complete` 가 멱등이라 괜찮습니다
      await markSubmitted(id).catch((err: unknown) => noteWriteFailure(id, 'ending', err));
      clearWriteFailures(id);
      clearClock(takeId);
      // 기록을 쌓지 않고 바꿉니다. 뒤로 가기로 리허설 화면에 돌아오지 않게 합니다
      navigate(`/takes/${takeId}/processing`, { replace: true });
    } catch (e) {
      // 기록은 브라우저에 그대로 있습니다. 여기서 잃는 것은 없고,
      // 재시도 화면이 같은 clientSessionId로 다시 보냅니다
      console.error('[rehearsal] 종료 처리 실패', e);
      setEndError(toMessage(e));
      navigate(`/takes/${takeId}/retry`, { state: { clientSessionId: id } });
    }
  };

  // 종료 중에 새로고침했습니다. 무대는 열지 않고 끝내기를 누른 순간 고정한 값으로 종료를 이어갑니다.
  // `/complete` 는 clientSessionId 로 멱등이라 새로고침 전에 이미 갔어도 다시 보내도 됩니다.
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
      // 기다린 뒤에야 마지막 전사를 정리하고, 그 사이 `/complete` 가 먼저 갑니다.
      // 붙어서 stop 만 보냅니다 (최대 3초). 이미 닿았으면 서버가 열었다 바로 닫습니다
      await stopTakeStream(takeId);
      await submit(sessionId, ending);
    })().catch((e: unknown) => {
      console.error('[rehearsal] 종료 처리 실패', e);
      setEndError(toMessage(e));
    });
  });

  const gazeNote = gazeNoteText({
    cameraLost: deviceError !== null,
    missingCalibration,
    error: gazeError,
    ready: gazeReady,
  });

  return (
    <div className="min-h-full bg-greige px-4 py-5">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
        <ScreenLabel
          screenNo="07"
          screenName="발표 연습"
          entry={`진입 · 리허설 준비의 Take ${ticket?.takeNumber ?? ''} 시작하기`}
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
                SLIDE {slideNumber} / {pageCount || '—'}
              </span>
            </header>

            <div className="viewport">
              <div className="slide">
                {/* PDF 한 장을 상자에 맞춰 그립니다. 못 열었으면 자리표시 — 깨진 화면보다 낫습니다 */}
                {materials.doc ? (
                  <PdfPage
                    doc={materials.doc}
                    pageNumber={slideNumber}
                    fit="contain"
                    className="h-full w-full"
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

              <div className="rail">
                <section className="camera">
                  <video ref={videoRef} muted playsInline />
                  {!live && <div className="guide" />}
                  <span className="tag">CAMERA 16:9</span>
                </section>

                <section className="mic">
                  <LevelBar variant="segments" meterRef={meterRef} />
                </section>

                <section className="next">
                  <div className="thumb">
                    {materials.doc && nextSlideNumber > 0 ? (
                      <PdfPage
                        doc={materials.doc}
                        pageNumber={nextSlideNumber}
                        fit="contain"
                        className="h-full w-full"
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

            <div className="coach" data-empty={coach === null}>
              <span>{coach?.text ?? ''}</span>
              <span className="rule">한 번에 하나만 · 8초 후 사라짐</span>
            </div>

            <ScriptPane
              mode={scriptMode}
              slideNumber={slideNumber}
              text={currentScript}
              keywords={currentKeywords}
              highlights={currentHighlights}
            />

            <div className="stage-foot">
              <span>{gazeNote}</span>
              {sttNote && (
                <span className="stt" data-alert={sttAlert}>
                  {sttNote}
                </span>
              )}
              {/* 실전 모드에서는 전사를 숨깁니다 — 자기 말이 글로 따라붙으면 그걸 읽게 됩니다.
                  기록은 그대로 서버에 쌓이니 리포트에서는 차이가 없습니다 */}
              {mode !== 'EXAM' && <span className="stt-text" ref={transcriptRef} />}
              {audioState !== null && audioState !== 'running' && (
                <span>소리가 흐르지 않습니다 — 화면을 한 번 클릭해 주세요</span>
              )}
              {mode === 'EXAM' && <span>실전 모드 — 발표 중에는 코치가 말하지 않습니다</span>}
              {ticket === null && (
                <span>연습 정보를 찾을 수 없어요. 장치 점검부터 다시 시작해 주세요.</span>
              )}
              {sessionError && <span>{sessionError}</span>}
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
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * 시선 안내 한 줄. 위에서부터 먼저 걸리는 사유 하나만 보여 줍니다.
 */
function gazeNoteText(state: {
  cameraLost: boolean;
  missingCalibration: boolean;
  error: GazeExcludedReason | null;
  ready: boolean;
}): string {
  if (state.cameraLost) return '카메라가 끊겼습니다 — 발표는 계속됩니다';
  if (state.missingCalibration) return '시선 기준이 없어 측정 제외 — 발표는 계속됩니다';
  if (state.error) return `시선 측정 제외 · ${state.error}`;
  if (state.ready) return '시선 기록 중';
  return '시선 엔진 준비 중';
}

function endButtonText(ending: boolean, confirming: boolean): string {
  if (ending) return '정리하는 중…';
  if (confirming) return '정말 끝낼까요?';
  return '발표 끝내기';
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
