import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { useGazeWorker } from '../media/useGazeWorker';
import { appendGazeDecision, loadZoneRef, markGazeExcluded } from '../lib/db';
import { noteWriteFailure } from '../lib/writeFailures';
import { fitsCurrentEngine } from '@/workers/calibrationModel';
import type { ZoneDecision } from '@/workers/gaze.contract';
import type { GazeExcludedReason, GazeZone, Ms } from '@/types/api';

/** contract의 Zone → 테두리 상태. 워커가 뭘 주든 매핑은 한 곳에서만 합니다 */
const BORDER: Record<GazeZone, string> = {
  CAMERA: 'AUDIENCE',
  BOTTOM: 'SCREEN',
  UNCERTAIN: 'UNKNOWN',
};

/** 코치 규칙이 보는 창. 1초 판정 기준이라 10개입니다 */
const WINDOW_MS = 10_000;

/**
 * 저장된 기준을 불러온 상태. 결과가 지금 키에 대한 것이 아니면 아직 불러오는 중입니다 —
 * 레이아웃이나 엔진이 바뀌면 이전 결과는 저절로 LOADING 으로 읽힙니다.
 */
function zoneRefState(
  layoutSignature: string | null,
  loadKey: string | null,
  loaded: { key: string; found: boolean } | null,
): 'LOADING' | 'READY' | 'MISSING' {
  if (layoutSignature === null) return 'MISSING';
  if (loadKey === null || loaded?.key !== loadKey) return 'LOADING';
  return loaded.found ? 'READY' : 'MISSING';
}

/**
 * 발표 중 시선.
 *
 * 프레임은 워커로 들어가고 **1초 판정만** 나옵니다. 원시 좌표는 메인 스레드로
 * 넘어오지 않습니다 — 그게 이 경계의 전부입니다 (CLAUDE.md 워커 경계).
 *
 * 판정이 도착하면 하는 일 셋. 셋 다 React를 거치지 않습니다 —
 *   1. 테두리 색: stage의 dataset 한 줄
 *   2. 기록: IndexedDB에 한 행 (★ 이게 없으면 리포트가 비어 있습니다)
 *   3. 최근 10초 창: 코치 규칙이 읽습니다
 *
 * 초당 한 번이라 setState를 해도 당장은 버틸 것 같지만, 같은 초에 시계·음량이
 * 함께 바뀝니다. 하나를 상태로 올리면 나머지도 따라 올라갑니다.
 *
 * ── 기준을 먼저 넣습니다 ────────────────────────────────────────────
 * 분류기는 그 사람의 캘리브레이션 기준이 없으면 판정하지 않습니다 (AI v1 도 같습니다).
 * 기준은 장치 점검 화면의 카메라 화면 모듈(다른 워커)에서 계산되어 IndexedDB 에 있으므로,
 * 꺼내서 이 워커에 넣은 **뒤에** 펌프를 켭니다.
 *
 * 기준을 못 찾으면(새로고침으로 요약이 사라짐 · 오래됨 · 저장 실패) 시선은
 * `ENGINE_UNAVAILABLE` 로 제외되고 발표는 계속됩니다 — 엔진이 판정할 수 없는 상태라서입니다.
 */
export function useLiveGaze({
  stream,
  videoRef,
  stageRef,
  clientSessionId,
  layoutSignature,
  enabled,
  onExcluded,
}: {
  stream: MediaStream | null;
  videoRef: RefObject<HTMLVideoElement>;
  stageRef: RefObject<HTMLDivElement>;
  clientSessionId: string | null;
  /**
   * 장치 점검에서 잡은 기준의 저장 키 (`CalibrationSummary.layoutSignature`).
   * 여기서 다시 계산하지 않습니다 — 리허설 스트림의 해상도가 조금만 달라도
   * 키가 바뀌어 방금 잡은 기준을 못 찾습니다.
   */
  layoutSignature: string | null;
  /** 시선 측정을 제외한 Take면 false — 펌프를 돌리지 않습니다 */
  enabled: boolean;
  /**
   * 제외 사유가 정해진 순간 **메모리로도** 알립니다.
   * IndexedDB 에 적는 것과 별개인 이유는, 적는 일 자체가 실패할 수 있기 때문입니다 —
   * 그때 이 값이 종료 페이로드의 마지막 근거가 됩니다.
   */
  onExcluded: (reason: GazeExcludedReason) => void;
}) {
  const recentRef = useRef<ZoneDecision[]>([]);
  // 판정 콜백이 읽을 세션 키. 이펙트에서 옮깁니다 — 렌더에서 ref 에 쓰면
  // 그것도 "렌더 중 ref 접근"입니다
  const sessionRef = useRef(clientSessionId);
  useEffect(() => {
    sessionRef.current = clientSessionId;
  }, [clientSessionId]);

  const onDecision = useCallback(
    (d: ZoneDecision) => {
      // 1. 테두리 — 리렌더 없이
      if (stageRef.current) stageRef.current.dataset.gaze = BORDER[d.zone];

      // 2. 기록 — 종료 시점에 이 행들을 구간으로 접어 서버로 보냅니다
      //
      // ★ 여기를 `.catch(() => undefined)` 로 두면 안 됩니다 (CLAUDE.md 9번).
      //   빠진 초는 화면에 아무 흔적도 남기지 않고, 종료 시점에 "화면 응시 62%" 같은
      //   숫자만 조용히 틀리게 만듭니다. 원본이 없으니 나중에 다시 계산할 수도 없습니다.
      //   그래서 실패를 감추는 대신 **믿을 수 없다는 사실을 기록에 고정**합니다.
      const id = sessionRef.current;
      if (id) {
        appendGazeDecision(id, d).catch((err: unknown) => {
          // 한 건이라도 빠지면 이 Take 의 시선 비율은 이미 틀렸습니다 —
          // 분모(발표 길이)는 그대로인데 분자에서만 빠지기 때문입니다.
          if (noteWriteFailure(id, 'gazeDecision', err)) {
            onExcluded('STORAGE_FAILED');
            // 이 표시까지 실패하면 호출부의 메모리 폴백이 받습니다
            markGazeExcluded(id, 'STORAGE_FAILED').catch((e: unknown) =>
              noteWriteFailure(id, 'gazeExcluded', e),
            );
          }
        });
      }

      // 3. 최근 창
      const recent = recentRef.current;
      recent.push(d);
      while (recent.length > 0 && d.tMs - recent[0]!.tMs > WINDOW_MS) recent.shift();
    },
    [stageRef, onExcluded],
  );

  const {
    ready,
    engineVersion,
    error: workerError,
    perf,
    startPump,
    stopPump,
    calibrate,
  } = useGazeWorker(
    onDecision,
    0,
    // AI 시선 엔진 v1.1 (`vendor/gaze/engine`). 바뀐 건 이 한 단어뿐입니다 —
    // 그게 계약 파일을 따로 둔 이유입니다. `/models/` 에 자산이 없으면 워커가
    // ENGINE_UNAVAILABLE 을 내고 시선만 제외된 채 발표는 계속됩니다
    'model',
  );

  /**
   * 기준을 불러온 결과. 어느 키·엔진에 대한 결과인지 함께 둡니다 —
   * 둘 중 하나가 바뀌면 이전 결과는 저절로 무효가 되어 LOADING 으로 읽힙니다.
   *
   * 엔진 버전은 워커가 ready 를 보내야 압니다. 그 전에는 비교할 수 없으니 기다립니다 —
   * 엔진이 끝내 못 뜨면 워커가 ENGINE_UNAVAILABLE 을 내고, 그게 제외 사유가 됩니다.
   */
  const [loaded, setLoaded] = useState<{ key: string; found: boolean } | null>(null);
  const loadKey =
    layoutSignature !== null && engineVersion !== null
      ? `${layoutSignature}#${engineVersion}`
      : null;
  const refState = zoneRefState(layoutSignature, loadKey, loaded);

  useEffect(() => {
    if (layoutSignature === null || engineVersion === null || loadKey === null) return;
    let cancelled = false;

    // 엔진이 다르면 null 입니다 — 다른 모델이 만든 기준은 넣지 않습니다
    loadZoneRef(layoutSignature, engineVersion)
      .then((stored) => {
        if (cancelled) return;
        // 설정이 바뀐 엔진의 기준은 없는 것으로 봅니다 — 버전 문자열은 같아도 판정 기준이 다릅니다.
        // 넣어 두면 판정이 하나도 안 나오는데 '기록 중'으로 보이므로, 여기서 MISSING 으로 돌립니다
        const ref = stored && fitsCurrentEngine(stored.model) ? stored : null;
        // 펌프보다 먼저 들어가야 합니다 — READY 가 되어야 아래 효과가 펌프를 켭니다
        if (ref) calibrate(ref);
        setLoaded({ key: loadKey, found: ref !== null });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ key: loadKey, found: false });
      });

    return () => {
      cancelled = true;
    };
  }, [layoutSignature, engineVersion, loadKey, calibrate]);

  const pumping = enabled && refState === 'READY';

  useEffect(() => {
    if (!pumping) return;
    const video = videoRef.current;
    if (!video || !stream) return;

    startPump(video);
    return () => stopPump();
  }, [pumping, stream, videoRef, startPump, stopPump]);

  // 기준이 없는 것은 발표를 시작한 뒤에만 사유가 됩니다 — enabled 가 false 인
  // 동안(카메라 권한 거부 등)은 다른 사유가 이미 정해져 있으니 덮지 않습니다
  const missingCalibration = enabled && refState === 'MISSING';
  const error = workerError ?? (missingCalibration ? 'ENGINE_UNAVAILABLE' : null);

  /**
   * 최근 창에서 화면(아래)을 본 비율. 표본이 모자라면 null —
   * 없는 근거로 코치하지 않습니다. 정체성을 고정해 두는 이유는 useStageClock 과 같습니다.
   */
  const bottomRatio = useCallback((windowMs: Ms = WINDOW_MS): number | null => {
    const recent = recentRef.current;
    if (recent.length === 0) return null;
    const last = recent.at(-1)!.tMs;
    const inWindow = recent.filter((d) => last - d.tMs <= windowMs);
    const measured = inWindow.filter((d) => d.zone !== 'UNCERTAIN');
    if (measured.length < 5) return null;
    return measured.filter((d) => d.zone === 'BOTTOM').length / measured.length;
  }, []);

  return { ready, engineVersion, error, perf, bottomRatio, missingCalibration };
}
