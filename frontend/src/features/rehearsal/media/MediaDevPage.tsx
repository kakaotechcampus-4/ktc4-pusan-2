import { useCallback, useEffect, useRef, useState } from 'react';
import { DEVICE_ERROR_MESSAGE, useCameraStream } from './useCameraStream';
import { useGazeWorker, type GazeImpl } from './useGazeWorker';
import {
  RECORDER_ERROR_MESSAGE,
  startRecording,
  type RecorderError,
  type RecordingHandle,
} from './recorder';
import { createLevelMeter, type LevelMeter } from './level';
import { TemporalVoter } from '@/workers/temporalVoter';
import { buildGazePayload } from '../lib/gazePayload';
import {
  appendGazeDecision,
  audioBytes,
  countAll,
  endSession,
  getSession,
  markGazeExcluded,
  readGazeDecisions,
  setEngineVersion,
  setGazePerf,
  startSession,
} from '../lib/db';
import type { ZoneDecision } from '@/workers/gaze.contract';
import type { GazeExcludedReason } from '@/types/api';

/** 재볼 부하 값. 15fps 가 나오는 지점이 모델 예산이다. */
const LOADS = [0, 20, 40, 60, 80] as const;

/** dev 페이지가 새로고침 뒤에도 같은 세션을 잇도록 — 제품 코드가 아니다 */
const SESSION_KEY = 'pitchcoach.devSession';

/**
 * /dev/media — 프레임 예산 계기판 + 기록/제외 경로 확인. 제품 화면이 아니다.
 *
 * 여기서 확인하는 것 넷:
 *   1. 부하별 처리 fps (프레임 예산)
 *   2. 판정이 IndexedDB 에 쌓이고 **새로고침해도 남는가**
 *   3. 모델이 없을 때 앱이 죽지 않고 "측정 제외"로 가는가
 *   4. dummy ↔ model 을 바꿔도 이 파일이 안 바뀌는가 (실제로 안 바뀐다 — impl 값만 넘긴다)
 */
export function MediaDevPage() {
  const { stream, error: deviceError, request, stop } = useCameraStream();
  const [loadMs, setLoadMs] = useState<number>(0);
  const [impl, setImpl] = useState<GazeImpl>('dummy');
  const pumping = stream !== null;

  const videoRef = useRef<HTMLVideoElement>(null);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [excluded, setExcluded] = useState<GazeExcludedReason | null>(null);
  const [payloadJson, setPayloadJson] = useState<string | null>(null);
  const [bytes, setBytes] = useState(0);
  const [recorderError, setRecorderError] = useState<RecorderError | null>(null);
  const [audioState, setAudioState] = useState<string | null>(null);
  // 녹음 여부는 시작·정지에 한 번씩만 바뀌므로 상태로 둔다.
  // ref 를 렌더에서 읽으면 갱신이 안 된다 — ref 는 렌더를 유발하지 않는다.
  const [recording, setRecording] = useState(false);

  // 녹음과 음량은 화면 상태로 올리지 않는다 — 조각은 IndexedDB 로, 레벨은 DOM 으로.
  const recorderRef = useRef<RecordingHandle | null>(null);
  const levelRef = useRef<LevelMeter | null>(null);
  const levelRafRef = useRef(0);
  const levelBarRef = useRef<HTMLDivElement>(null);
  const silentRef = useRef<HTMLSpanElement>(null);

  // ★ T8 판정 — 음량 바가 움직여도 이 숫자는 안 올라야 한다.
  //   올라가면 어딘가에서 setState 를 하고 있다는 뜻이고, 그 상태로 초당 수십 번
  //   레벨이 갱신되면 시선 프레임 펌프가 같은 스레드에서 밀린다.
  const renderCountRef = useRef(0);
  const renderBadgeRef = useRef<HTMLElement>(null);

  // 판정은 초당 하나지만 React 상태로 올리지 않는다 — DOM 에 직접 쓴다.
  // 실시간 코치가 붙어도 같은 방식이다.
  const sessionIdRef = useRef<string | null>(null);
  const decisionCountRef = useRef(0);
  const countCellRef = useRef<HTMLSpanElement>(null);
  const zoneCellRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    renderCountRef.current += 1;
    if (renderBadgeRef.current) renderBadgeRef.current.textContent = String(renderCountRef.current);
  });

  /** 음량 바 — 매 프레임 DOM 에 직접 쓴다. 이름 붙인 함수 표현식이라 자기 참조가 안전하다 */
  const levelLoop = useCallback(function loop() {
    levelRafRef.current = requestAnimationFrame(loop);
    const meter = levelRef.current;
    if (!meter) return;

    // 말하기 RMS 는 대략 0.05~0.2 다. 400배가 화면에서 읽을 만한 폭이 된다.
    const rms = meter.read();
    if (levelBarRef.current) {
      levelBarRef.current.style.width = `${Math.min(100, rms * 400).toFixed(1)}%`;
    }
    if (silentRef.current) {
      const ms = meter.silentMs();
      // 권한은 살아 있는데 입력이 없는 상황 — 이어폰 분리 · 다른 앱 점유 · 시스템 음소거
      silentRef.current.textContent =
        ms > 3000 ? `${Math.floor(ms / 1000)}초째 무음 — 마이크 입력이 없습니다` : '';
    }
  }, []);

  const refreshCounts = useCallback(async (id: string) => {
    setCounts(await countAll(id));
    setBytes(await audioBytes(id));
    const row = await getSession(id);
    setExcluded(row?.gazeExcluded ? row.gazeExcludedReason : null);
  }, []);

  // 새로고침해도 같은 세션을 잇는다 — T9 완료 판정("숫자가 남아 있다")을 보려면 필요하다.
  useEffect(() => {
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) return;
    void (async () => {
      const row = await getSession(saved);
      if (!row) {
        localStorage.removeItem(SESSION_KEY);
        return;
      }
      sessionIdRef.current = saved;
      setSessionId(saved);
      await refreshCounts(saved);
    })();
  }, [refreshCounts]);

  const ensureSession = useCallback(async () => {
    if (sessionIdRef.current) return sessionIdRef.current;
    const id = await startSession();
    localStorage.setItem(SESSION_KEY, id);
    sessionIdRef.current = id;
    setSessionId(id);
    return id;
  }, []);

  const onDecision = useCallback((d: ZoneDecision) => {
    decisionCountRef.current += 1;
    if (countCellRef.current) countCellRef.current.textContent = String(decisionCountRef.current);
    if (zoneCellRef.current) {
      zoneCellRef.current.textContent = `${d.zone} (표본 ${d.sampleCount}, conf ${d.confidence.toFixed(2)})`;
    }
    // ★ 판정을 붙잡아 둔다. 이게 없으면 파이프라인의 산출물이 화면에 찍히고 사라진다.
    const id = sessionIdRef.current;
    if (id) void appendGazeDecision(id, d);
  }, []);

  const {
    ready,
    engineVersion,
    error: workerError,
    perf,
    startPump,
    stopPump,
  } = useGazeWorker(onDecision, loadMs, impl);

  // 스트림이 붙으면 비디오에 물리고 펌프를 시작한다.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    if (!stream) {
      video.srcObject = null;
      stopPump();
      return;
    }

    video.srcObject = stream;
    let cancelled = false;
    video
      .play()
      .then(async () => {
        if (cancelled) return;
        const id = await ensureSession();
        if (cancelled) return;

        // 시선 tMs 와 오디오 offsetMs 가 **같은 기준**이어야 나중에 두 기록을
        // 겹쳐 볼 수 있다. 여기서 한 번 잡아 둘에 같이 넘긴다.
        // (startPump 는 내부에서 다시 now() 를 읽어 몇 ms 어긋나지만,
        //  판정 주기가 1000ms 라 무시할 수 있는 차이다.)
        const t0 = performance.now();

        decisionCountRef.current = 0;
        startPump(video);

        // 녹음 — 원본. 실패해도 발표는 계속 간다.
        const rec = startRecording(stream, id, t0, setRecorderError);
        recorderRef.current = rec.handle;
        setRecording(rec.handle !== null);
        setRecorderError(rec.error);

        // 음량 — AudioContext 는 여기서 만든다. 제스처 뒤라 resume() 이 통한다.
        const meter = await createLevelMeter(stream);
        if (cancelled) {
          meter?.stop();
          return;
        }
        levelRef.current = meter;
        setAudioState(meter?.state ?? null);
        cancelAnimationFrame(levelRafRef.current);
        levelRafRef.current = requestAnimationFrame(levelLoop);
      })
      .catch((e: unknown) => {
        // ★ 빈 catch 를 두지 않는다. 여기서 삼키면 녹음 시작 실패 같은 진짜 문제가
        //   "아무 일도 안 일어남"으로 보인다. 실제로 그래서 한참 헤맸다:
        //   recorder.start() 가 throw 했고, 그 뒤의 음량계까지 같이 죽었는데
        //   화면에는 아무 표시가 없었다.
        console.error('[dev/media] start failed', e);
      });

    return () => {
      cancelled = true;
    };
  }, [stream, startPump, stopPump, ensureSession, levelLoop]);

  // 언마운트에서 음량 루프를 끈다. 남겨 두면 HMR 마다 rAF 가 쌓인다.
  useEffect(
    () => () => {
      cancelAnimationFrame(levelRafRef.current);
      levelRafRef.current = 0;
      levelRef.current?.stop();
      levelRef.current = null;
    },
    [],
  );

  // 엔진 버전은 종료 시점에 영구 고정된다 — ready 를 받는 즉시 기록해 둔다.
  // ★ 워커가 보낸 실제 문자열을 쓴다. 자리표시(`${impl}-engine`)를 넣으면
  //   어느 모델 파일이 낸 숫자인지 알 수 없어져서, 기록의 의미가 사라진다.
  useEffect(() => {
    if (!ready || !engineVersion || !sessionId) return;
    void setEngineVersion(sessionId, engineVersion).then(() => refreshCounts(sessionId));
  }, [ready, engineVersion, sessionId, refreshCounts]);

  // 1초마다 오는 perf 를 clientPerf 용으로 남긴다.
  useEffect(() => {
    if (!perf || !sessionId) return;
    void setGazePerf(sessionId, perf.avgFps, perf.droppedFrames);
  }, [perf, sessionId]);

  // ── 제외 사유 배선 ──────────────────────────────────────────────────
  // 워커 에러(엔진 없음 · 카메라 소실)와 권한 거부를 각각 다른 사유로 남긴다.
  // 사유가 문구를 결정하므로 뭉치면 사용자가 뭘 해야 할지 모른다.
  useEffect(() => {
    if (!workerError || !sessionId) return;
    void markGazeExcluded(sessionId, workerError).then(() => refreshCounts(sessionId));
  }, [workerError, sessionId, refreshCounts]);

  useEffect(() => {
    if (deviceError !== 'PERMISSION_DENIED' || !sessionId) return;
    void markGazeExcluded(sessionId, 'USER_DECLINED').then(() => refreshCounts(sessionId));
  }, [deviceError, sessionId, refreshCounts]);

  const handleStop = () => {
    stopPump();

    cancelAnimationFrame(levelRafRef.current);
    levelRafRef.current = 0;
    levelRef.current?.stop();
    levelRef.current = null;
    if (levelBarRef.current) levelBarRef.current.style.width = '0%';
    if (silentRef.current) silentRef.current.textContent = '';

    // 마지막 조각까지 받고 멈춘다 — 기다리지 않으면 마지막 5초가 잘린다.
    const rec = recorderRef.current;
    recorderRef.current = null;
    setRecording(false);
    void rec?.stop().then(() => {
      const id = sessionIdRef.current;
      if (id) void refreshCounts(id);
    });

    stop();
  };

  /** 종료 → 서버로 보낼 GazePayload 를 조립해 본다 */
  const handleComplete = async () => {
    const id = sessionIdRef.current;
    if (!id) return;
    handleStop();
    await endSession(id);

    const row = await getSession(id);
    const decisions = await readGazeDecisions(id);
    const durationMs =
      decisions.length === 0 ? 0 : decisions.at(-1)!.tMs + TemporalVoter.INTERVAL_MS;

    const result = buildGazePayload({
      decisions,
      durationMs,
      engineVersion: row?.engineVersion ?? null,
      decisionIntervalMs: TemporalVoter.INTERVAL_MS,
      excludedReason: row?.gazeExcluded ? row.gazeExcludedReason : null,
      // Calibration 은 아직 만드는 코드가 없다 (W3 · AI팀 4번 답 대기)
      calibration: null,
    });

    setPayloadJson(
      JSON.stringify(
        {
          gaze: result.payload,
          // 리포트에서 null 이 될 값들 — 여기서는 계산 결과를 그대로 보여 준다
          derived: { cameraMs: result.cameraMs, bottomMs: result.bottomMs },
          validationErrors: result.validationErrors,
          clientPerf: {
            avgGazeFps: row?.gazeAvgFps ?? null,
            droppedFrames: row?.gazeDroppedFrames ?? 0,
            degradedToLightAtMs: null,
          },
        },
        null,
        2,
      ),
    );
    await refreshCounts(id);
  };

  const handleNewSession = async () => {
    handleStop();
    localStorage.removeItem(SESSION_KEY);
    sessionIdRef.current = null;
    setSessionId(null);
    setCounts(null);
    setExcluded(null);
    setPayloadJson(null);
    decisionCountRef.current = 0;
    if (countCellRef.current) countCellRef.current.textContent = '0';
    const id = await ensureSession();
    await refreshCounts(id);
  };

  const dropped = perf?.droppedFrames ?? 0;

  return (
    <div className="min-h-full bg-greige p-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <header className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-bold text-coral-deep">DEV</span>
            <h1 className="text-xl font-bold">프레임 예산 · 기록 · 제외 경로</h1>
          </div>
          <div className="flex items-baseline gap-3">
            <p className="text-sm text-stone">
              부하를 올려 가며 <b className="text-ink">처리 fps</b>를 읽습니다. 15fps가 나오는
              지점이 모델 예산입니다.
            </p>
            <span className="tabular ml-auto shrink-0 text-xs text-stone">
              렌더{' '}
              <b className="text-ink" ref={renderBadgeRef}>
                0
              </b>{' '}
              — 음량 바로는 안 올라야 정상
            </span>
          </div>
        </header>

        {/* ★ 이 문장이 없으면 내일 아침에 본인이 버그로 착각한다 */}
        <div className="rounded-xl border border-line bg-coral-wash/60 p-4 text-sm leading-relaxed">
          <b>zone이 계속 `UNCERTAIN`으로 나옵니다 — 추론이 없으니 정상입니다.</b>
          <br />
          워커가 만드는 샘플이 <code>faceFound: false</code>라 <code>classify()</code>가{' '}
          <code>null</code>을 내고, 표본 0이라 <code>MIN_SAMPLES</code> 규칙이 걸립니다.
        </div>

        {/* 조작 */}
        <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => void request()}
              disabled={pumping}
              className="rounded-full bg-coral px-4 py-1.5 text-xs font-semibold text-white
                         hover:bg-coral-deep disabled:opacity-40"
            >
              카메라 시작
            </button>
            <button
              onClick={handleStop}
              disabled={!pumping}
              className="rounded-full border border-line bg-cream px-4 py-1.5 text-xs font-semibold
                         hover:bg-coral-wash disabled:opacity-40"
            >
              정지
            </button>
            <button
              onClick={() => void handleComplete()}
              disabled={!sessionId}
              className="rounded-full border border-line bg-cream px-4 py-1.5 text-xs font-semibold
                         hover:bg-coral-wash disabled:opacity-40"
            >
              종료 · 페이로드 조립
            </button>
            <button
              onClick={() => void handleNewSession()}
              className="ml-auto rounded-full border border-line bg-cream px-3 py-1.5 text-xs
                         font-semibold text-stone hover:bg-coral-wash"
            >
              새 세션
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-bold text-stone">가짜 부하</span>
            {LOADS.map((ms) => (
              <button
                key={ms}
                onClick={() => setLoadMs(ms)}
                className={`tabular rounded-full border px-3 py-1.5 text-xs font-semibold ${
                  loadMs === ms
                    ? 'border-coral bg-coral text-white'
                    : 'border-line bg-cream hover:bg-coral-wash'
                }`}
              >
                {ms}ms
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-bold text-stone">분류기</span>
            {(['dummy', 'model'] as GazeImpl[]).map((v) => (
              <button
                key={v}
                onClick={() => setImpl(v)}
                className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${
                  impl === v
                    ? 'border-coral bg-coral text-white'
                    : 'border-line bg-cream hover:bg-coral-wash'
                }`}
              >
                {v}
              </button>
            ))}
            <span className="text-xs text-stone">
              `model`은 <code>/models/gaze.onnx</code>가 없으면 <b>측정 제외</b>로 갑니다 — 앱은 안
              죽습니다
            </span>
          </div>
        </section>

        {/* 계기판 */}
        <section className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
          <Cell label="워커">
            {workerError ? (
              <span className="text-coral-deep">{workerError}</span>
            ) : ready ? (
              <span className="text-ink">ready</span>
            ) : (
              <span className="text-stone">로딩 중…</span>
            )}
          </Cell>

          <Cell label="처리 fps" strong>
            {perf ? perf.avgFps.toFixed(1) : '—'}
          </Cell>

          <Cell label="프레임당 ms">{perf ? `${perf.msPerFrame.toFixed(1)}ms` : '—'}</Cell>

          <Cell label="버린 프레임">
            {dropped === 0 ? (
              <span>0</span>
            ) : (
              <span className="text-coral-deep">
                {dropped} — 백프레셔가 새고 있습니다. 이 상태의 fps는 신뢰할 수 없습니다
              </span>
            )}
          </Cell>

          <Cell label="판정 수 (이번 실행)">
            <span ref={countCellRef}>0</span>
          </Cell>

          <Cell label="마지막 판정">
            <span ref={zoneCellRef} className="text-stone">
              —
            </span>
          </Cell>

          <Cell label="기록 (IndexedDB)">
            {counts ? (
              <span>
                시선 <b>{counts.gazeSegments}</b>건
                <span className="ml-2 text-stone">
                  · 슬라이드 {counts.slideChanges} · 오디오 {counts.audioChunks}
                </span>
              </span>
            ) : (
              <span className="text-stone">세션 없음</span>
            )}
            <div className="mt-1 text-xs text-stone">새로고침해도 이 숫자가 남아야 정상입니다</div>
          </Cell>

          <Cell label="음량 (RMS)">
            <div className="h-3 w-full overflow-hidden rounded-full bg-cream">
              {/* 폭을 매 프레임 DOM 에 직접 쓴다 — 상태로 올리면 초당 수십 번 렌더된다 */}
              <div ref={levelBarRef} className="h-full bg-coral" style={{ width: '0%' }} />
            </div>
            <div className="mt-1 text-xs text-coral-deep">
              <span ref={silentRef} />
            </div>
            {audioState && audioState !== 'running' && (
              <div className="mt-1 text-xs text-coral-deep">
                AudioContext가 `{audioState}`입니다 — 오디오가 흐르지 않습니다
              </div>
            )}
          </Cell>

          <Cell label="녹음">
            {recorderError ? (
              <span className="text-coral-deep">{recorderError}</span>
            ) : recording ? (
              <span>
                {counts?.audioChunks ?? 0}조각
                <span className="ml-2 text-stone">{(bytes / 1024).toFixed(0)}KB · 5초 단위</span>
              </span>
            ) : (
              <span className="text-stone">멈춤 · {counts?.audioChunks ?? 0}조각 저장됨</span>
            )}
          </Cell>

          <Cell label="시선 측정">
            {excluded ? (
              <span className="text-coral-deep">제외 · {excluded}</span>
            ) : (
              <span className="text-ink">측정 중</span>
            )}
          </Cell>
        </section>

        {sessionId && <p className="font-mono text-xs text-stone">clientSessionId: {sessionId}</p>}

        {/* 장치 에러 — useCameraStream 의 문구를 그대로 쓴다. 새로 만들지 않는다 */}
        {deviceError && (
          <div className="rounded-xl border border-coral bg-panel p-4 text-sm">
            <b className="text-coral-deep">{deviceError}</b>
            <p className="mt-1 leading-relaxed">{DEVICE_ERROR_MESSAGE[deviceError]}</p>
          </div>
        )}

        {recorderError && (
          <div className="rounded-xl border border-coral bg-panel p-4 text-sm">
            <b className="text-coral-deep">{recorderError}</b>
            <p className="mt-1 leading-relaxed">{RECORDER_ERROR_MESSAGE[recorderError]}</p>
          </div>
        )}

        <video
          ref={videoRef}
          muted
          playsInline
          className="w-[320px] rounded-lg border border-line bg-stage-deep"
        />

        {payloadJson && (
          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-bold">조립된 GazePayload</h2>
            <p className="text-xs leading-relaxed text-stone">
              제외된 Take 는 <code>excluded: true</code> + 빈 구간 + 0 을 보냅니다. 여기서 0 은
              &ldquo;0% 봤다&rdquo;가 아니라 &ldquo;측정한 구간이 없다&rdquo;는 뜻이고,{' '}
              <b>리포트의 null 은 서버가 만듭니다.</b>
            </p>
            <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-panel p-3 font-mono text-xs">
              {payloadJson}
            </pre>
          </section>
        )}

        <p className="text-xs leading-relaxed text-stone">
          측정할 때는 어댑터를 꽂고, 워밍업 5초를 버리고, 30초 이상 재세요. 30초 동안 fps가 계속
          떨어지면 메모리가 쌓이는 것입니다.
        </p>
      </div>
    </div>
  );
}

function Cell({
  label,
  children,
  strong = false,
}: {
  label: string;
  children: React.ReactNode;
  strong?: boolean;
}) {
  return (
    <div className="bg-panel p-4">
      <div className="text-xs font-bold text-stone">{label}</div>
      <div className={`tabular mt-1 ${strong ? 'text-2xl font-bold' : 'text-sm'}`}>{children}</div>
    </div>
  );
}
