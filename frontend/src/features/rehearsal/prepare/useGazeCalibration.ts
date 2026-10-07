import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { useGazeWorker } from '../media/useGazeWorker';
import { loadZoneRef, saveZoneRef } from '../lib/db';
import { readLayoutSignature } from '../lib/layoutSignature';
import type {
  CalibrationFailReason,
  CalibrationResult,
  PlacementResult,
  ZoneDecision,
} from '@/workers/gaze.contract';
import { usePrepareStore } from './prepareStore';

/**
 * 한 점을 잡는 시간. AI 설정의 camera_seconds · bottom_seconds 와 같은 값이고,
 * 배치 확인의 camera_seconds · screen_seconds 도 같은 2초입니다
 */
export const HOLD_MS = 2_000;

/**
 * 프레임을 뜨는 간격. 2초에 16장쯤 모입니다 —
 * 분류기가 요구하는 클래스당 최소 표본(10장)보다 넉넉하고,
 * 그렇다고 4초분이 메모리에 과하게 쌓이지도 않는 선입니다.
 */
const GRAB_EVERY_MS = 125;

/**
 * 앞의 넷(PLACE_*)이 카메라 배치 확인, 뒤가 2점 캘리브레이션입니다.
 *
 *   PLACE_CAMERA → PLACE_SCREEN → PLACE_EVALUATING ┬ TOP      → CAMERA → BOTTOM → EVALUATING
 *                                                 └ TOP 아님 → PLACE_WARN (다시 확인 / 이대로 계속)
 */
export type CalibrationPhase =
  | 'IDLE'
  | 'PLACE_CAMERA'
  | 'PLACE_SCREEN'
  | 'PLACE_EVALUATING'
  | 'PLACE_WARN'
  | 'CAMERA'
  | 'BOTTOM'
  | 'EVALUATING'
  | 'DONE'
  | 'FAILED';

/** 프레임을 모으거나 분류기를 기다리는 중 — 버튼을 다시 눌러도 새로 시작하지 않습니다 */
const RUNNING: readonly CalibrationPhase[] = [
  'PLACE_CAMERA',
  'PLACE_SCREEN',
  'PLACE_EVALUATING',
  'CAMERA',
  'BOTTOM',
  'EVALUATING',
];

/**
 * 배치 경고 문구. 위치를 알면 위치로, 판별이 어려우면 사유로 고릅니다 —
 * AI 데모(`pipeline_demo.py`)가 안내문을 고르는 규칙과 같습니다.
 *
 * 모두 "참고용"입니다. 사용자가 안내 지점을 잘못 봐도 이렇게 나올 수 있습니다.
 */
export function placementMessage(r: PlacementResult): string {
  switch (r.placement) {
    case 'BOTTOM':
      return '카메라가 화면 아래에 있는 것 같아요. 이대로면 카메라를 볼 때와 대본을 볼 때가 뒤바뀌어 기록될 수 있어요.';
    case 'SIDE_LEFT':
    case 'SIDE_RIGHT':
      return '카메라가 화면 옆에 있는 것 같아요. 이대로면 대본을 보는 시선을 제대로 가려내지 못할 수 있어요.';
    case 'TOP':
      return '';
    case 'INCONCLUSIVE':
      break;
  }
  switch (r.reason) {
    case 'NOT_ENOUGH_SAMPLES':
      return '얼굴이 잡힌 장면이 모자라 카메라 위치를 판단하지 못했어요. 얼굴 전체가 화면 안에 들어오게 해 주세요.';
    case 'TARGETS_NOT_SEPARATED':
    case 'DISPLACEMENT_TOO_SMALL':
      return '렌즈를 볼 때와 화면 가운데를 볼 때 시선 차이가 거의 없었어요. 두 지점으로 눈을 분명히 옮겨 봐 주세요.';
    case 'AMBIGUOUS_AXIS':
      return '카메라가 화면 위인지 옆인지 가리기 어려웠어요. 카메라를 화면 위 가운데에 두면 가장 정확해요.';
    case 'ENGINE_ERROR':
    case 'OK':
      return '카메라 위치를 확인하지 못했어요. 카메라가 화면 위에 있다면 그대로 계속해도 돼요.';
  }
}

/**
 * 재시도 안내. **사유마다 할 일이 다르므로** 문구도 다릅니다.
 *
 * AI 도 영어 안내문(`hint`)을 주지만 쓰지 않습니다 — 화면 문구는 FE 가 정합니다.
 * 뜻은 AI v1 `calibration/quality.py` 의 `_HINTS` 를 따랐습니다.
 */
export const CALIBRATION_FAIL_MESSAGE: Record<CalibrationFailReason, string> = {
  NOT_ENOUGH_SAMPLES:
    '얼굴이 잡힌 장면이 모자랐어요. 얼굴 전체가 화면 안에 들어오게 하고, 안내가 끝날 때까지 시선을 유지해 주세요.',
  DEGENERATE_FEATURES:
    '시선 값이 전혀 바뀌지 않았어요. 카메라가 가려지지 않았는지, 조명이 너무 어둡지 않은지 확인해 주세요.',
  CLASS_NOT_SEPARABLE:
    '카메라를 볼 때와 대본 자리를 볼 때가 거의 같아 보여요. 처음에는 렌즈를 똑바로, 다음에는 화면 아래 가운데를 분명히 봐 주세요.',
  LOW_LOO_ACCURACY:
    '기준이 흔들렸어요. 자세를 고정하고 카메라와 거리를 그대로 둔 채 다시 해주세요.',
  CENTROIDS_TOO_CLOSE:
    '두 지점을 볼 때 시선 차이가 너무 작았어요. 대본 자리를 볼 때 눈을 조금 더 아래로 내려 주세요.',
  ENGINE_ERROR: '시선 분석이 잠시 멈췄어요. 다시 시도해도 안 되면 페이지를 새로고침해 주세요.',
};

/** 잡은 기준점 수. 아래 자리까지 넘어왔으면 카메라 자리는 이미 잡힌 것입니다 */
function calibrationPoints(phase: CalibrationPhase): number {
  if (phase === 'DONE') return 2;
  if (phase === 'BOTTOM' || phase === 'EVALUATING') return 1;
  return 0;
}

/**
 * 카메라 배치 확인(렌즈 2초 · 화면 가운데 2초) → 2점 캘리브레이션(카메라 2초 · 화면 아래 2초).
 *
 * ── 배치 확인은 막지 않습니다 ───────────────────────────────────────
 * 웹캠이 화면 아래·옆에 있으면 CAMERA/BOTTOM 이 조용히 뒤집혀 기록됩니다.
 * 그래서 먼저 보지만, 결과는 **참고용 추정**이라 경고만 하고 "이대로 계속"을 열어 둡니다.
 * AI 데모도 같은 정책입니다 — 안내를 잘못 따른 것 하나로 캘리브레이션을 막지 않습니다.
 *
 * 같은 배치(layoutSignature)에서 이미 확인했으면 다시 잡을 때 건너뜁니다.
 *
 * ── 잡아 둔 기준이 틀려지는 두 경우를 막습니다 ────────────────────
 * 1. **배치가 바뀜** — 카메라를 바꾸거나 해상도·화면이 달라지면, 잡아 둔 기준·배치 기록을
 *    버리고 처음부터 받습니다. 진행 중이었으면 멈춥니다(두 카메라의 프레임이 섞입니다).
 * 2. **다시 들어왔는데 저장된 기준이 없음** — 요약은 메모리에 남아 "완료"로 보이는데
 *    IndexedDB 의 기준은 오래됐거나 다른 엔진 것일 수 있습니다. 들어올 때 실제로 꺼내 보고,
 *    없으면 처음부터 받습니다. 확인이 끝나기 전에는 시작하지 못하게 `verifying` 을 둡니다.
 *
 * ── 분업 (A안) ──────────────────────────────────────────────────────
 * FE 는 **프레임을 모아 넘기고 화면을 안내**합니다.
 * 기준을 계산하고 품질을 판정하는 것은 분류기입니다 —
 * `fitCalibration(camera[], bottom[])` 이 `CalibrationResult` 를 돌려주고,
 * `ok: false` 면 품질 미달이라 `failReason` 에 맞춰 재시도를 안내합니다.
 *
 * 그래서 이 파일에는 임계값이 없습니다. 어디서 자를지는 분류기가 압니다.
 *
 * ── 왜 발표 전에 잡아야 하나 ────────────────────────────────────────
 * 개인화에 주어지는 건 4초가 전부이고, 그 위에서 그 Take 의 모든 시선 숫자가
 * 계산됩니다. 서버는 재계산할 수 없습니다. 그래서 여기서 걸러야 합니다.
 *
 * ── 기준을 어디에 두는가 ────────────────────────────────────────────
 * `ZoneReference` 는 IndexedDB 에만 둡니다. 서버로는 품질 요약만 갑니다
 * (CLAUDE.md 1번). `model` 안은 FE 가 해석하지 않습니다 — 저장하고 되돌려줄 뿐입니다.
 *
 * 저장 키(`layoutSignature`)는 여기서 만듭니다 — 프레임을 모으기 시작할 때의
 * 카메라·해상도·화면으로 정합니다. 결과가 돌아올 때 읽으면 그사이 바뀐 값이 섞입니다.
 */
export function useGazeCalibration({
  videoRef,
  live,
}: {
  videoRef: RefObject<HTMLVideoElement>;
  /** 영상이 실제로 흐르고 있나. 프레임이 없으면 모을 것도 없습니다 */
  live: boolean;
}) {
  const summary = usePrepareStore((s) => s.calibration);
  const setCalibration = usePrepareStore((s) => s.setCalibration);
  const clearCalibration = usePrepareStore((s) => s.clearCalibration);
  const advice = usePrepareStore((s) => s.calibrationAdvice);
  const placement = usePrepareStore((s) => s.placement);
  const setPlacement = usePrepareStore((s) => s.setPlacement);

  const [phase, setPhase] = useState<CalibrationPhase>(summary ? 'DONE' : 'IDLE');
  /** 마지막 실패 사유. 화면이 사유별 안내를 고릅니다 */
  const [failReason, setFailReason] = useState<CalibrationFailReason | null>(null);
  /** 들어올 때 있던 요약이 IndexedDB 의 기준과 맞는지 아직 확인 중 */
  const [verifying, setVerifying] = useState(summary !== null);
  /**
   * 분류기에 기준 계산을 맡기고 결과를 기다리는 중. 그사이 무효화되면 false 로 내려서
   * 늦게 도착한 결과를 버립니다 — 안 버리면 옛 카메라의 기준이 새 카메라 것으로 저장됩니다.
   */
  const fitPendingRef = useRef(false);
  /** 이번 시도의 저장 키. start() 에서 정하고 결과가 오면 씁니다 */
  const signatureRef = useRef('');
  /**
   * 기준 저장이 끝나는 시점. 리허설이 IndexedDB 에서 꺼내 쓰므로
   * 저장이 끝나기 전에 넘어가면 방금 잡은 기준을 못 찾습니다.
   */
  const savingRef = useRef<Promise<void>>(Promise.resolve());
  /** 기준을 IndexedDB 에 저장하지 못함. 리허설이 기준을 못 찾으므로 시작을 막습니다 */
  const [saveFailed, setSaveFailed] = useState(false);

  /** 남은 초는 DOM 에 직접 씁니다 — 4초 동안 렌더를 열 번 돌릴 이유가 없습니다 */
  const countdownRef = useRef<HTMLSpanElement>(null);
  const tickRef = useRef(0);
  /**
   * 지금 살아 있는 시도의 번호. 시작할 때 올리고, 취소할 때도 올립니다.
   * 각 단계는 자기 번호가 아직 현재 번호인지 보고 이어갑니다.
   *
   * 참/거짓 깃발로 두면 안 됩니다 — 취소한 직후 다시 시작하면 깃발이 도로 내려가서,
   * 아직 기다리던 옛 시도가 깨어나 새 시도와 나란히 프레임을 모읍니다.
   */
  const attemptRef = useRef(0);
  /**
   * 배치 결과를 기다리는 쪽. 워커 결과는 콜백으로 오고 흐름은 async 라,
   * 둘을 이 한 칸으로 잇습니다. 결과가 오면 비웁니다.
   */
  const placementWaitRef = useRef<((r: PlacementResult) => void) | null>(null);

  const onCalibrated = useCallback(
    (result: CalibrationResult, engineVersion: string) => {
      // 기다리던 결과가 아닙니다 — 그사이 카메라가 바뀌어 이 시도는 취소됐습니다
      if (!fitPendingRef.current) return;
      fitPendingRef.current = false;

      if (!result.ok) {
        // 모델을 만들지 못했습니다. 왜인지는 분류기가 알려주고, 화면은 그 사유에 맞게 안내합니다
        setFailReason(result.reason);
        setPhase('FAILED');
        return;
      }

      const { ref } = result;
      const layoutSignature = signatureRef.current;
      const id = attemptRef.current;
      setFailReason(null);
      setPhase('DONE');
      // 기준은 브라우저에만 남습니다. 다음 Take 가 같은 기기·배치·엔진이면 되살려 씁니다
      // 실패는 삼키지 않습니다 — 화면은 '완료'인데 리허설에서 시선이 조용히 빠지기 때문입니다.
      // 시작은 막고, 다시 잡게 합니다
      savingRef.current = saveZoneRef(layoutSignature, ref, engineVersion);
      savingRef.current.catch(() => {
        // 그사이 다시 잡기를 눌렀으면 옛 시도의 실패로 새 시도를 막지 않습니다
        if (attemptRef.current === id) setSaveFailed(true);
      });
      setCalibration(
        {
          points: 2,
          quality: ref.quality,
          // 수치를 모르는 분류기(더미)면 null 입니다 —
          // 0 으로 채우면 "분리도가 0" 이라는 뜻이 되어 리포트가 거짓말을 합니다
          separability: ref.metrics?.separability ?? null,
          // 분류기에는 <video> 원본 프레임을 넘깁니다. 화면에 보이는 거울상은
          // CSS 변환이라 createImageBitmap 결과에는 반영되지 않습니다.
          // AI v1 도 거울 반전이 없는 이미지로 부호를 검증했습니다
          coordinateSpace: 'raw',
          layoutSignature,
        },
        // 품질이 낮으면(POOR) 다시 잡기를 권합니다. 막지는 않습니다 — AI 정책입니다
        result.advice,
      );
    },
    [setCalibration],
  );

  // 판정(decision)은 장치 점검에서 쓰지 않습니다 — 테두리를 그리는 화면이 아닙니다.
  // 워커는 기준을 잡기 위해 띄웁니다.
  const noop = useCallback((_d: ZoneDecision) => {}, []);
  const onPlacementChecked = useCallback((r: PlacementResult) => {
    placementWaitRef.current?.(r);
    placementWaitRef.current = null;
  }, []);
  const { ready, error, engineVersion, fitCalibration, checkPlacement } = useGazeWorker(
    noop,
    0,
    'dummy',
    {
      onCalibrated,
      onPlacementChecked,
    },
  );

  const clearTimers = useCallback(() => {
    window.clearInterval(tickRef.current);
    if (countdownRef.current) countdownRef.current.textContent = '';
  }, []);

  useEffect(
    () => () => {
      attemptRef.current++;
      clearTimers();
    },
    [clearTimers],
  );

  /**
   * 잡아 둔 것을 모두 버리고 처음 상태로 돌아갑니다. 진행 중이면 멈춥니다 —
   * 모으던 프레임은 각 단계가 시도 번호를 보고 닫고, 기다리던 결과는 버립니다.
   */
  const invalidate = useCallback(() => {
    attemptRef.current++;
    clearTimers();
    fitPendingRef.current = false;
    placementWaitRef.current = null;
    clearCalibration();
    setFailReason(null);
    setVerifying(false);
    setPhase('IDLE');
  }, [clearTimers, clearCalibration]);

  // ── 1. 배치가 바뀌면 버립니다 ────────────────────────────────────────
  //
  // <video> 가 알려 줍니다. 카메라를 바꾸면 srcObject 가 갈리면서 `emptied` 가 오고,
  // 새 영상의 크기가 정해지면 `loadedmetadata`, 도중에 해상도가 바뀌면 `resize` 가 옵니다.
  // 그때마다 지금 키를 잡아 둔 기준의 키와 비교합니다.
  //
  // 리스너가 최신 상태를 읽도록 ref 에 옮겨 둡니다 — 상태가 바뀔 때마다
  // 리스너를 떼었다 붙이면 그 사이에 온 이벤트를 놓칩니다.
  const running = RUNNING.includes(phase);
  const latestRef = useRef({ summary, placement, phase });
  useEffect(() => {
    latestRef.current = { summary, placement, phase };
  }, [summary, placement, phase]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const check = () => {
      const { summary, placement, phase } = latestRef.current;
      const inProgress = RUNNING.includes(phase) || phase === 'PLACE_WARN';

      // 영상이 비었습니다(카메라 교체 중·끊김). 진행 중인 시도는 이어갈 수 없습니다
      if (video.videoWidth === 0) {
        if (RUNNING.includes(phase)) invalidate();
        return;
      }

      const now = readLayoutSignature(video);
      const stale =
        (summary !== null && summary.layoutSignature !== now) ||
        // 진행 중·경고 중인 시도는 start() 에서 정한 키와 비교합니다
        (inProgress && signatureRef.current !== now);
      if (stale) {
        invalidate();
        return;
      }
      // 기준은 멀쩡한데 배치 기록만 다른 배치 것이면 그것만 지웁니다
      if (placement?.layoutSignature && placement.layoutSignature !== now) setPlacement(null);
    };

    video.addEventListener('emptied', check);
    video.addEventListener('loadedmetadata', check);
    video.addEventListener('resize', check);
    return () => {
      video.removeEventListener('emptied', check);
      video.removeEventListener('loadedmetadata', check);
      video.removeEventListener('resize', check);
    };
  }, [videoRef, invalidate, setPlacement]);

  // ── 2. 다시 들어왔으면 저장된 기준이 실제로 있는지 봅니다 ────────────
  //
  // 엔진 버전을 알아야 비교할 수 있어 워커가 뜬 뒤에 봅니다.
  // 엔진이 못 떴으면 확인할 방법이 없으니 확인을 끝냅니다 — 리허설이 어차피 제외합니다.
  useEffect(() => {
    if (!verifying || error) return;
    if (!summary || !engineVersion) return;

    let cancelled = false;
    loadZoneRef(summary.layoutSignature, engineVersion)
      .then((ref) => {
        if (cancelled) return;
        if (ref === null) invalidate();
        else setVerifying(false);
      })
      .catch(() => {
        if (!cancelled) invalidate();
      });
    return () => {
      cancelled = true;
    };
  }, [verifying, error, summary, engineVersion, invalidate]);

  /**
   * 한 자리를 바라보는 동안 프레임을 뜹니다.
   *
   * 화면에 남은 초를 쓰면서 모읍니다. 실패하면(비디오가 아직 준비 전이면)
   * 그 장만 건너뜁니다 — 몇 장 모자란 것은 분류기가 표본 수로 잡아냅니다.
   */
  const collect = useCallback(
    async (video: HTMLVideoElement, id: number): Promise<ImageBitmap[]> => {
      const frames: ImageBitmap[] = [];
      const endAt = performance.now() + HOLD_MS;

      window.clearInterval(tickRef.current);
      tickRef.current = window.setInterval(() => {
        const left = Math.max(0, endAt - performance.now());
        if (countdownRef.current) countdownRef.current.textContent = `${Math.ceil(left / 1000)}`;
      }, 100);

      while (performance.now() < endAt && attemptRef.current === id) {
        try {
          frames.push(await createImageBitmap(video));
        } catch {
          // 이 장만 버립니다. 카메라가 빠졌다면 아래에서 표본 부족으로 걸립니다
        }
        await new Promise((r) => window.setTimeout(r, GRAB_EVERY_MS));
      }

      window.clearInterval(tickRef.current);
      if (countdownRef.current) countdownRef.current.textContent = '';
      return frames;
    },
    [],
  );

  /** 2점 캘리브레이션. 배치 확인을 통과했거나 사용자가 "이대로 계속"을 눌렀을 때 옵니다 */
  const runCalibration = useCallback(
    async (video: HTMLVideoElement, id: number) => {
      setPhase('CAMERA');
      const camera = await collect(video, id);

      if (attemptRef.current !== id) {
        for (const b of camera) b.close();
        return;
      }

      setPhase('BOTTOM');
      const bottom = await collect(video, id);

      if (attemptRef.current !== id) {
        for (const b of [...camera, ...bottom]) b.close();
        return;
      }

      setPhase('EVALUATING');
      // 여기서부터는 분류기 몫입니다. 비트맵은 넘어가고, 닫는 것도 워커가 합니다
      fitPendingRef.current = true;
      if (!fitCalibration(camera, bottom)) {
        fitPendingRef.current = false;
        setFailReason('ENGINE_ERROR');
        setPhase('FAILED');
      }
    },
    [collect, fitCalibration],
  );

  /** 카메라 배치 확인. 결과를 돌려줍니다 — 중간에 취소되면 null 입니다 */
  const runPlacement = useCallback(
    async (video: HTMLVideoElement, id: number): Promise<PlacementResult | null> => {
      setPhase('PLACE_CAMERA');
      const camera = await collect(video, id);
      if (attemptRef.current !== id) {
        for (const b of camera) b.close();
        return null;
      }

      setPhase('PLACE_SCREEN');
      const screen = await collect(video, id);
      if (attemptRef.current !== id) {
        for (const b of [...camera, ...screen]) b.close();
        return null;
      }

      setPhase('PLACE_EVALUATING');
      const result = new Promise<PlacementResult>((resolve) => {
        placementWaitRef.current = resolve;
      });
      if (!checkPlacement(camera, screen)) {
        placementWaitRef.current = null;
        return { placement: 'INCONCLUSIVE', supported: false, reason: 'ENGINE_ERROR' };
      }
      return result;
    },
    [collect, checkPlacement],
  );

  const start = useCallback(() => {
    const video = videoRef.current;
    if (!video || !live) return;

    const id = ++attemptRef.current;
    setSaveFailed(false);
    const layoutSignature = readLayoutSignature(video);
    signatureRef.current = layoutSignature;

    (async () => {
      // 같은 배치에서 이미 확인했으면(통과했거나 경고를 보고 계속했으면) 건너뜁니다
      if (placement?.layoutSignature !== layoutSignature) {
        const result = await runPlacement(video, id);
        if (!result || attemptRef.current !== id) return;

        if (!result.supported) {
          // 경고만 합니다. 이 배치를 "확인 끝"으로 치는 것은 "이대로 계속"을 누를 때입니다 —
          // 그래서 키를 비워 둡니다. 비어 있으면 [다시 확인]이 배치부터 다시 잽니다
          setPlacement({ ...result, overridden: false, layoutSignature: '' });
          setPhase('PLACE_WARN');
          return;
        }
        setPlacement({ ...result, overridden: false, layoutSignature });
      }

      await runCalibration(video, id);
    })().catch(() => undefined);
  }, [videoRef, live, placement, runPlacement, runCalibration, setPlacement]);

  /**
   * 배치 경고를 보고도 진행합니다. 막지 않는 대신 **진행했다는 사실을 남깁니다** —
   * 나중에 그 Take 의 시선 숫자가 이상하면 이것이 첫 번째 단서입니다.
   */
  const continueAnyway = useCallback(() => {
    const video = videoRef.current;
    if (!video || !live || !placement || phase !== 'PLACE_WARN') return;

    const id = ++attemptRef.current;
    setPlacement({ ...placement, overridden: true, layoutSignature: signatureRef.current });
    runCalibration(video, id).catch(() => undefined);
  }, [videoRef, live, placement, phase, setPlacement, runCalibration]);

  const saved = useCallback(() => savingRef.current, []);

  const points = calibrationPoints(phase);

  return {
    phase,
    /** FAILED 일 때 왜 실패했는지. 그 외에는 null */
    failReason: phase === 'FAILED' ? failReason : null,
    /** DONE 이지만 품질이 낮을 때(POOR) 다시 잡기를 권하는 이유. 그 외에는 null */
    advice: phase === 'DONE' ? advice : null,
    points,
    running,
    /**
     * 들어올 때 있던 기준을 확인 중. 끝나기 전에는 시작하지 않습니다.
     * 엔진이 못 떴으면 확인할 방법이 없어 끝난 것으로 봅니다 — 리허설이 어차피 제외합니다
     */
    verifying: verifying && !error,
    summary,
    /** PLACE_WARN 일 때 보여줄 배치 결과. 그 외에는 null */
    placementWarning: phase === 'PLACE_WARN' ? placement : null,
    countdownRef,
    start,
    continueAnyway,
    /** 기준 저장이 끝날 때까지 기다립니다. 리허설로 넘어가기 직전에 부릅니다 */
    saved,
    /** 기준을 저장하지 못함. 시작을 막고 다시 잡기를 안내합니다 */
    saveFailed,
    /** 워커가 떴나 · 엔진이 없으면 사유 */
    engineReady: ready,
    engineError: error,
  };
}
