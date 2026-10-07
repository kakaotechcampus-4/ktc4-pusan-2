import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { GazeCameraView, type CameraPhase, type SetupResult } from '@/vendor/gaze/camera';
import { CALIBRATION_HINT } from '@/vendor/gaze/camera/text';
import type { StoredModel } from '@/vendor/gaze/engine';
import { takeEngineVersion, toCalibrationResult, toPlacementResult } from '@/workers/aiAdapter';
import { fitsCurrentEngine } from '@/workers/calibrationModel';
import type { CalibrationFailReason } from '@/workers/gaze.contract';
import { loadZoneRef, saveZoneRef } from '../lib/db';
import { readLayoutSignature } from '../lib/layoutSignature';
import { usePrepareStore } from './prepareStore';

/**
 * 재시도 안내. **사유마다 할 일이 다르므로** 문구도 다릅니다.
 *
 * 카메라 화면 모듈이 결과 카드에 쓰는 문구(`vendor/gaze/camera/text.ts`)를 그대로 씁니다 —
 * 카드와 옆 안내가 다른 말을 하면 사용자가 무엇을 고칠지 헷갈립니다.
 * `ENGINE_ERROR` 만 FE 몫입니다.
 */
export const CALIBRATION_FAIL_MESSAGE: Record<CalibrationFailReason, string> = {
  NOT_ENOUGH_SAMPLES: CALIBRATION_HINT.NOT_ENOUGH_SAMPLES!,
  DEGENERATE_FEATURES: CALIBRATION_HINT.DEGENERATE_FEATURES!,
  CLASS_NOT_SEPARABLE: CALIBRATION_HINT.CLASS_NOT_SEPARABLE!,
  LOW_LOO_ACCURACY: CALIBRATION_HINT.LOW_LOO_ACCURACY!,
  CENTROIDS_TOO_CLOSE: CALIBRATION_HINT.CENTROIDS_TOO_CLOSE!,
  ANCHOR_AMBIGUOUS: CALIBRATION_HINT.ANCHOR_AMBIGUOUS!,
  ENGINE_ERROR: '시선 분석이 잠시 멈췄어요. 다시 시도해도 안 되면 페이지를 새로고침해 주세요.',
};

/**
 * 화면이 보는 시선 기준 상태.
 *
 *   LOADING      엔진(MediaPipe)을 불러오는 중
 *   UNAVAILABLE  엔진이 못 떴음 — `/models/` 에 자산이 없을 때가 대표적. 소리만으로 갑니다
 *   IDLE         아직 안 잡음
 *   RUNNING      준비 점검 · 고개 원 · 3점 보정 중 (전체 화면)
 *   DONE         기준이 있음 (품질이 낮으면 `advice`)
 *   FAILED       모델을 만들지 못함 — 다시 잡아야 합니다
 */
export type GazeSetupStatus = 'LOADING' | 'UNAVAILABLE' | 'IDLE' | 'RUNNING' | 'DONE' | 'FAILED';

/** 카메라 화면 모듈의 단계 중 보정이 진행 중인 것. 결과 카드(`result`)는 이미 결과가 나온 뒤입니다 */
const RUNNING_PHASES: ReadonlySet<CameraPhase> = new Set(['align', 'sweep', 'swept', 'calib']);
/** 전체 화면으로 띄우는 단계 — 보정 중과 결과 카드 */
const OVERLAY_PHASES: ReadonlySet<CameraPhase> = new Set([...RUNNING_PHASES, 'result']);

/**
 * 장치 점검의 시선 기준 — AI 카메라 화면 모듈(`GazeCameraView`, v1.1)에 맡깁니다.
 *
 * ── 분업 ────────────────────────────────────────────────────────────
 * 상자 **안**은 모듈이 그립니다: 준비 점검(한 명 · 가운데 · 거리 · 밝기) → 고개 원 →
 * 3점 보정(화면 가운데 → 렌즈 → 대본 자리) → 결과 카드 → 실시간 확인.
 * 카메라 위치가 화면 아래·옆이면 모듈이 멈추고 "이대로 계속"을 묻습니다.
 * FE 는 상자 · 스트림 · 결과 처리 셋을 맡습니다 (AI `web/README.md` "카메라 화면 붙이기").
 *
 * 그래서 이 파일에는 임계값도 화면 문구도 거의 없습니다. 어디서 자를지는 엔진이 압니다.
 *
 * ── 전체 화면인 이유 ────────────────────────────────────────────────
 * 모듈은 렌즈 과녁을 상자 위 가운데에, 대본 자리를 상자 아래 180px 띠에 둡니다.
 * 상자가 화면 전체여야 그 띠가 리허설 무대의 대본 영역(`--spacing-script-highlight`, 180px)과
 * 같은 자리가 됩니다. 카드 크기 상자에서 잡으면 대본을 보는 각도가 달라집니다.
 * 보정이 끝나면 카드로 돌아와 실시간 확인을 보여 줍니다.
 *
 * ── 결과 처리 (FE 몫) ───────────────────────────────────────────────
 * `calibrated` 가 오면 —
 *   모델 없음        → FAILED. 사유에 맞춰 안내
 *   모델 있음        → `ZoneReference` 를 IndexedDB 에 저장(리허설 워커가 꺼내 씀),
 *                      품질 요약을 스토어에 (시작 CTA 가 서버로 보냄)
 *   배치             → 스토어에. 경고를 보고 "이대로 계속"을 눌렀으면 `overridden`
 *
 * 저장 키는 `layoutSignature` + 엔진 버전입니다. 엔진 버전은 리허설 워커와 **같은 함수**
 * (`takeEngineVersion`)로 만듭니다 — 다르면 리허설이 기준을 못 찾습니다.
 *
 * ── 잡아 둔 기준이 틀려지는 두 경우를 막습니다 ────────────────────
 * 1. **배치가 바뀜** — 카메라를 바꾸거나 해상도·화면이 달라지면 버리고 처음부터 받습니다.
 *    진행 중이었으면 멈춥니다.
 * 2. **다시 들어왔는데 저장된 기준이 없음** — 들어올 때 IndexedDB 에서 실제로 꺼내 보고,
 *    있으면 모듈에 되돌려 실시간 확인을 켜고, 없으면 처음부터 받습니다.
 *    확인이 끝나기 전에는 시작하지 못하게 `verifying` 을 둡니다.
 */
export function useGazeSetup({
  stream,
  videoRef,
  live,
}: {
  stream: MediaStream | null;
  /**
   * 화면에 보이지 않는 FE 의 `<video>`. 모듈은 자기 `<video>` 를 따로 가지므로,
   * 이것은 **배치 키(`layoutSignature`)와 카메라 교체 감지**에만 씁니다.
   */
  videoRef: RefObject<HTMLVideoElement>;
  /** 영상이 실제로 흐르고 있나. 프레임이 없으면 잡을 것도 없습니다 */
  live: boolean;
}) {
  const summary = usePrepareStore((s) => s.calibration);
  const setCalibration = usePrepareStore((s) => s.setCalibration);
  const clearCalibration = usePrepareStore((s) => s.clearCalibration);
  const advice = usePrepareStore((s) => s.calibrationAdvice);
  const setPlacement = usePrepareStore((s) => s.setPlacement);

  /** 모듈이 그릴 상자. 크기는 FE 가 정합니다 (카드 · 전체 화면) */
  const boxRef = useRef<HTMLDivElement>(null);
  /**
   * 전체 화면으로 올릴 틀 — 상자와 [그만하기] 버튼을 함께 감쌉니다.
   * 상자만 올리면 전체 화면 밖의 버튼은 보이지 않습니다.
   */
  const frameRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<GazeCameraView | null>(null);

  /** 모듈 엔진 버전 (`ready`). 이게 있어야 저장·비교할 수 있습니다 */
  const [version, setVersion] = useState<string | null>(null);
  const [engineFailed, setEngineFailed] = useState(false);
  const [phase, setPhase] = useState<CameraPhase>('idle');
  /** 마지막 실패 사유. 화면이 사유별 안내를 고릅니다 */
  const [failReason, setFailReason] = useState<CalibrationFailReason | null>(null);
  /** 들어올 때 있던 요약이 IndexedDB 의 기준과 맞는지 아직 확인 중 */
  const [verifying, setVerifying] = useState(summary !== null);
  /** 기준을 IndexedDB 에 저장하지 못함. 리허설이 기준을 못 찾으므로 시작을 막습니다 */
  const [saveFailed, setSaveFailed] = useState(false);

  /**
   * 지금 살아 있는 시도의 번호. 시작할 때 올리고, 무효화할 때도 올립니다.
   * 저장 실패가 늦게 와도 자기 시도의 것일 때만 반영합니다.
   */
  const attemptRef = useRef(0);
  /**
   * 이번 시도의 결과를 받을지. 무효화되면 false 로 내려서 늦게 도착한 결과를 버립니다 —
   * 안 버리면 옛 카메라의 기준이 새 카메라 것으로 저장됩니다.
   */
  const pendingRef = useRef(false);
  /** 이번 시도의 저장 키. 시도가 시작될 때 정하고 결과가 오면 씁니다 */
  const signatureRef = useRef('');
  /**
   * 기준 저장이 끝나는 시점. 리허설이 IndexedDB 에서 꺼내 쓰므로
   * 저장이 끝나기 전에 넘어가면 방금 잡은 기준을 못 찾습니다.
   */
  const savingRef = useRef<Promise<void>>(Promise.resolve());
  const versionRef = useRef<string | null>(null);

  /**
   * 잡아 둔 것을 모두 버리고 처음 상태로 돌아갑니다. 진행 중이면 멈춥니다.
   */
  const invalidate = useCallback(() => {
    attemptRef.current++;
    pendingRef.current = false;
    clearCalibration();
    setFailReason(null);
    setVerifying(false);
    const view = viewRef.current;
    if (view && view.phase !== 'idle') view.cancel();
  }, [clearCalibration]);

  /** 새 시도가 시작됐습니다 — 앞 결과를 지우고 이번 결과를 기다립니다 */
  const beginAttempt = useCallback(() => {
    const video = videoRef.current;
    attemptRef.current++;
    pendingRef.current = true;
    signatureRef.current = video ? readLayoutSignature(video) : '';
    clearCalibration();
    setFailReason(null);
    setSaveFailed(false);
    setVerifying(false);
  }, [videoRef, clearCalibration]);

  const onCalibrated = useCallback(
    (r: SetupResult) => {
      // 기다리던 결과가 아닙니다 — 그사이 카메라가 바뀌어 이 시도는 취소됐습니다
      if (!pendingRef.current) return;
      pendingRef.current = false;

      const layoutSignature = signatureRef.current;
      const id = attemptRef.current;

      // 배치는 모델이 없어도 남깁니다. 다음 시도에서 같은 경고가 왜 뜨는지의 단서입니다.
      // 모듈은 TOP 이 아닌 위치에서 멈추고 묻습니다 — 그때 진행했으면 forced 입니다
      if (r.placement) {
        const placement = toPlacementResult(r.placement);
        const warned = !placement.supported && placement.placement !== 'INCONCLUSIVE';
        setPlacement({ ...placement, overridden: warned && r.forced, layoutSignature });
      }

      const result = toCalibrationResult(r.quality, r.model);
      if (!result.ok) {
        // 모델을 만들지 못했습니다. 모듈의 결과 카드가 [다시 보정]을 띄웁니다
        setFailReason(result.reason);
        return;
      }

      const { ref } = result;
      const engine = versionRef.current;
      // ready 전에는 보정이 시작되지 않으므로 여기까지 오면 버전이 있습니다
      if (engine === null) {
        setFailReason('ENGINE_ERROR');
        return;
      }

      setFailReason(null);
      // 기준은 브라우저에만 남습니다. 다음 Take 가 같은 기기·배치·엔진이면 되살려 씁니다.
      // 실패는 삼키지 않습니다 — 화면은 '완료'인데 리허설에서 시선이 조용히 빠지기 때문입니다.
      // 시작은 막고, 다시 잡거나 '소리만으로 계속하기'로 가게 합니다
      savingRef.current = saveZoneRef(layoutSignature, ref, takeEngineVersion(engine));
      savingRef.current.catch(() => {
        // 그사이 다시 잡기를 눌렀으면 옛 시도의 실패로 새 시도를 막지 않습니다
        if (attemptRef.current === id) setSaveFailed(true);
      });
      setCalibration(
        {
          points: 3,
          quality: ref.quality,
          separability: ref.metrics?.separability ?? null,
          // 엔진에는 원본 프레임이 갑니다. 화면의 거울상은 모듈의 CSS 변환이라
          // createImageBitmap 결과에는 반영되지 않습니다
          coordinateSpace: 'raw',
          layoutSignature,
        },
        // 품질이 낮으면(POOR) 다시 잡기를 권합니다. 막지는 않습니다 — AI 정책입니다
        result.advice,
      );
    },
    [setCalibration, setPlacement],
  );

  // 콜백을 ref 로 — 바뀔 때마다 모듈을 다시 만들면 카메라 화면이 깜빡이고 엔진을 다시 띄웁니다
  const handlersRef = useRef({ onCalibrated, beginAttempt });
  useEffect(() => {
    handlersRef.current = { onCalibrated, beginAttempt };
  }, [onCalibrated, beginAttempt]);

  // ── 모듈 생명주기 ──────────────────────────────────────────────────
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;

    const view = new GazeCameraView(box, { assetDir: '/models/' });
    viewRef.current = view;

    const offs = [
      view.on('ready', ({ version: v }) => {
        versionRef.current = v;
        setVersion(v);
      }),
      // ready 전의 오류는 엔진이 못 뜬 것입니다. 뒤의 오류는 프레임 하나의 실패라
      // 모듈이 알림으로 띄우고 계속 돕니다
      view.on('error', () => {
        if (!view.ready) setEngineFailed(true);
      }),
      view.on('phase', ({ phase: next, previous }) => {
        // 준비 점검부터, 또는 결과·실시간에서 [다시 보정]으로 들어오면 새 시도입니다.
        // align → sweep → swept → calib 은 한 시도입니다
        const fresh = (next === 'align' || next === 'calib') && !RUNNING_PHASES.has(previous);
        if (fresh) handlersRef.current.beginAttempt();
        // 도중에 그만뒀으면(Esc · 그만하기 · 카메라 교체) 이 시도의 결과는 받지 않습니다
        if (next === 'idle') pendingRef.current = false;
        setPhase(next);
      }),
      view.on('calibrated', (r) => handlersRef.current.onCalibrated(r)),
    ];

    return () => {
      for (const off of offs) off();
      // Worker 도 같이 닫힙니다. 스트림은 useCameraStream 이 닫습니다
      view.destroy();
      viewRef.current = null;
      versionRef.current = null;
    };
  }, []);

  // 스트림 — 모듈은 보여 주고 분석만 합니다. 트랙을 멈추지 않습니다
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    if (!stream) {
      view.detach();
      return;
    }
    view.attach(stream).catch((e: unknown) => {
      // 삼키면 '아무 일도 안 일어남'으로 보입니다
      console.error('[device-check] gaze camera view attach failed', e);
    });
  }, [stream]);

  // ── 1. 배치가 바뀌면 버립니다 ────────────────────────────────────────
  //
  // FE 의 <video> 가 알려 줍니다. 카메라를 바꾸면 srcObject 가 갈리면서 `emptied` 가 오고,
  // 새 영상의 크기가 정해지면 `loadedmetadata`, 도중에 해상도가 바뀌면 `resize` 가 옵니다.
  //
  // 리스너가 최신 상태를 읽도록 ref 에 옮겨 둡니다 — 상태가 바뀔 때마다
  // 리스너를 떼었다 붙이면 그 사이에 온 이벤트를 놓칩니다.
  const latestRef = useRef({ summary, phase });
  useEffect(() => {
    latestRef.current = { summary, phase };
  }, [summary, phase]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const check = () => {
      const { summary, phase } = latestRef.current;
      const running = RUNNING_PHASES.has(phase);

      // 영상이 비었습니다(카메라 교체 중·끊김). 진행 중인 시도는 이어갈 수 없습니다
      if (video.videoWidth === 0) {
        if (running) invalidate();
        return;
      }

      const now = readLayoutSignature(video);
      const stale =
        (summary !== null && summary.layoutSignature !== now) ||
        (running && signatureRef.current !== now);
      if (stale) invalidate();
    };

    video.addEventListener('emptied', check);
    video.addEventListener('loadedmetadata', check);
    video.addEventListener('resize', check);
    return () => {
      video.removeEventListener('emptied', check);
      video.removeEventListener('loadedmetadata', check);
      video.removeEventListener('resize', check);
    };
  }, [videoRef, invalidate]);

  // ── 2. 다시 들어왔으면 저장된 기준이 실제로 있는지 봅니다 ────────────
  //
  // 엔진 버전을 알아야 비교할 수 있어 모듈이 뜬 뒤에 봅니다. 찾으면 모듈에 되돌려
  // 실시간 확인을 켭니다 — 사용자가 기준이 살아 있는 것을 눈으로 봅니다.
  // 엔진이 못 떴으면 확인할 방법이 없으니 확인을 끝냅니다 — 리허설이 어차피 제외합니다.
  useEffect(() => {
    if (!verifying || engineFailed) return;
    if (!summary || !version) return;

    let cancelled = false;
    loadZoneRef(summary.layoutSignature, takeEngineVersion(version))
      .then(async (ref) => {
        if (cancelled) return;
        const view = viewRef.current;
        // 설정이 바뀐 엔진의 보정이면 쓰지 않습니다. 모듈은 모양(schema)만 보고 설정은 확인하지 않아,
        // 그대로 넘기면 옛 기준으로 판정합니다 (fitsCurrentEngine). 모양이 다르면 모듈이 false 를 줍니다
        const ok =
          ref !== null &&
          fitsCurrentEngine(ref.model) &&
          view !== null &&
          (await view.useCalibration(ref.model as StoredModel));
        if (cancelled) return;
        if (ok) setVerifying(false);
        else invalidate();
      })
      .catch(() => {
        if (!cancelled) invalidate();
      });
    return () => {
      cancelled = true;
    };
  }, [verifying, engineFailed, summary, version, invalidate]);

  // ── 전체 화면 ──────────────────────────────────────────────────────
  //
  // 보정 중(결과 카드 포함)에는 상자를 화면 전체로 띄웁니다. 끝나면 카드로 돌아옵니다.
  // 사용자가 Esc 로 빠져나오면 보정을 멈춥니다 — 카드 크기로 이어 잡으면 각도가 틀립니다.
  const overlay = OVERLAY_PHASES.has(phase);

  useEffect(() => {
    if (overlay) return;
    if (document.fullscreenElement && document.fullscreenElement === frameRef.current) {
      document.exitFullscreen().catch(() => undefined);
    }
  }, [overlay]);

  useEffect(() => {
    if (!overlay) return;
    const stop = () => {
      const view = viewRef.current;
      if (view && OVERLAY_PHASES.has(view.phase)) view.cancel();
    };
    // 전체 화면이 거절된 브라우저에서도 Esc 로 나갈 수 있게 키도 봅니다
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') stop();
    };
    const onFullscreen = () => {
      if (document.fullscreenElement !== frameRef.current) stop();
    };
    window.addEventListener('keydown', onKey);
    document.addEventListener('fullscreenchange', onFullscreen);
    return () => {
      window.removeEventListener('keydown', onKey);
      document.removeEventListener('fullscreenchange', onFullscreen);
    };
  }, [overlay]);

  const status: GazeSetupStatus = (() => {
    if (engineFailed && !version) return 'UNAVAILABLE';
    if (!version) return 'LOADING';
    if (RUNNING_PHASES.has(phase)) return 'RUNNING';
    if (failReason) return 'FAILED';
    if (summary) return 'DONE';
    return 'IDLE';
  })();

  /**
   * 보정을 시작합니다. **사용자 클릭 안에서** 불러야 합니다 — 전체 화면은 그때만 열립니다.
   * 전체 화면이 거절돼도(아이프레임 등) 상자가 화면을 덮으므로 그대로 진행합니다.
   */
  const start = useCallback(() => {
    const view = viewRef.current;
    const frame = frameRef.current;
    if (!view || !frame || !live || !view.ready || RUNNING_PHASES.has(view.phase)) return;
    frame.requestFullscreen?.().catch(() => undefined);
    view.startSetup('align');
  }, [live]);

  /** 보정을 그만둡니다 (전체 화면의 [그만하기]) */
  const cancel = useCallback(() => {
    const view = viewRef.current;
    if (view && OVERLAY_PHASES.has(view.phase)) view.cancel();
  }, []);

  const saved = useCallback(() => savingRef.current, []);

  return {
    status,
    /** FAILED 일 때 왜 실패했는지. 그 외에는 null */
    failReason: status === 'FAILED' ? failReason : null,
    /** DONE 이지만 품질이 낮을 때(POOR) 다시 잡기를 권하는 이유. 그 외에는 null */
    advice: status === 'DONE' ? advice : null,
    /**
     * 들어올 때 있던 기준을 확인 중. 끝나기 전에는 시작하지 않습니다.
     * 엔진이 못 떴으면 확인할 방법이 없어 끝난 것으로 봅니다 — 리허설이 어차피 제외합니다
     */
    verifying: verifying && !engineFailed,
    /** 상자를 화면 전체로 띄울지 — 보정 중과 결과 카드 */
    overlay,
    boxRef,
    frameRef,
    start,
    cancel,
    /** 기준 저장이 끝날 때까지 기다립니다. 리허설로 넘어가기 직전에 부릅니다 */
    saved,
    /** 기준을 저장하지 못함. 시작을 막고 다시 잡기나 소리만으로 계속하기를 안내합니다 */
    saveFailed,
  };
}
