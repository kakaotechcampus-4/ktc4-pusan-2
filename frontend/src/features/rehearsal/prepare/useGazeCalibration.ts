import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { useGazeWorker } from '@/features/rehearsal/media/useGazeWorker';
import { saveZoneRef } from '@/features/rehearsal/lib/db';
import type { ZoneDecision, ZoneReference } from '@/workers/gaze.contract';
import { usePrepareStore } from './prepareStore';

/** 한 점을 잡는 시간. AI 설정의 camera_seconds · bottom_seconds 와 같은 값입니다 */
export const HOLD_MS = 2_000;

/**
 * 프레임을 뜨는 간격. 2초에 16장쯤 모입니다 —
 * 분류기가 요구하는 클래스당 최소 표본(10장)보다 넉넉하고,
 * 그렇다고 4초분이 메모리에 과하게 쌓이지도 않는 선입니다.
 */
const GRAB_EVERY_MS = 125;

export type CalibrationPhase = 'IDLE' | 'CAMERA' | 'BOTTOM' | 'EVALUATING' | 'DONE' | 'FAILED';

/**
 * 2점 캘리브레이션 — 카메라 2초, 화면 아래 2초.
 *
 * ── 분업 (A안) ──────────────────────────────────────────────────────
 * FE 는 **프레임을 모아 넘기고 화면을 안내**합니다.
 * 기준을 계산하고 품질을 판정하는 것은 분류기입니다 —
 * `fitCalibration(camera[], bottom[])` 이 `ZoneReference` 를 돌려주고,
 * `null` 이면 품질 미달이라 재시도해야 합니다.
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

  const [phase, setPhase] = useState<CalibrationPhase>(summary ? 'DONE' : 'IDLE');

  /** 남은 초는 DOM 에 직접 씁니다 — 4초 동안 렌더를 열 번 돌릴 이유가 없습니다 */
  const countdownRef = useRef<HTMLSpanElement>(null);
  const tickRef = useRef(0);
  const cancelledRef = useRef(false);

  const onCalibrated = useCallback(
    (ref: ZoneReference | null) => {
      if (ref === null) {
        // 품질 미달. 왜 미달인지는 분류기가 알고, 화면은 다시 잡으라고만 합니다
        setPhase('FAILED');
        return;
      }

      setPhase('DONE');
      // 기준은 브라우저에만 남습니다. 다음 Take 가 같은 기기·배치면 되살려 씁니다
      saveZoneRef(ref).catch(() => undefined);
      setCalibration({
        points: 2,
        quality: ref.quality,
        // 분류기는 등급만 줍니다. 수치는 모르는 값이라 null 입니다 —
        // 0 으로 채우면 "분리도가 0" 이라는 뜻이 되어 리포트가 거짓말을 합니다
        separability: null,
        // 분류기에는 <video> 원본 프레임을 넘깁니다. 화면에 보이는 거울상은
        // CSS 변환이라 createImageBitmap 결과에는 반영되지 않습니다
        coordinateSpace: 'raw',
        layoutSignature: ref.layoutSignature,
      });
    },
    [setCalibration],
  );

  // 판정(decision)은 장치 점검에서 쓰지 않습니다 — 테두리를 그리는 화면이 아닙니다.
  // 워커는 기준을 잡기 위해 띄웁니다.
  const noop = useCallback((_d: ZoneDecision) => {}, []);
  const { ready, error, fitCalibration } = useGazeWorker(noop, 0, 'dummy', onCalibrated);

  const clearTimers = useCallback(() => {
    window.clearInterval(tickRef.current);
    if (countdownRef.current) countdownRef.current.textContent = '';
  }, []);

  useEffect(
    () => () => {
      cancelledRef.current = true;
      clearTimers();
    },
    [clearTimers],
  );

  /**
   * 한 자리를 바라보는 동안 프레임을 뜹니다.
   *
   * 화면에 남은 초를 쓰면서 모읍니다. 실패하면(비디오가 아직 준비 전이면)
   * 그 장만 건너뜁니다 — 몇 장 모자란 것은 분류기가 표본 수로 잡아냅니다.
   */
  const collect = useCallback(async (video: HTMLVideoElement): Promise<ImageBitmap[]> => {
    const frames: ImageBitmap[] = [];
    const endAt = performance.now() + HOLD_MS;

    window.clearInterval(tickRef.current);
    tickRef.current = window.setInterval(() => {
      const left = Math.max(0, endAt - performance.now());
      if (countdownRef.current) countdownRef.current.textContent = `${Math.ceil(left / 1000)}`;
    }, 100);

    while (performance.now() < endAt && !cancelledRef.current) {
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
  }, []);

  const start = useCallback(() => {
    const video = videoRef.current;
    if (!video || !live) return;

    cancelledRef.current = false;

    (async () => {
      setPhase('CAMERA');
      const camera = await collect(video);

      if (cancelledRef.current) {
        for (const b of camera) b.close();
        return;
      }

      setPhase('BOTTOM');
      const bottom = await collect(video);

      if (cancelledRef.current) {
        for (const b of [...camera, ...bottom]) b.close();
        return;
      }

      setPhase('EVALUATING');
      // 여기서부터는 분류기 몫입니다. 비트맵은 넘어가고, 닫는 것도 워커가 합니다
      if (!fitCalibration(camera, bottom)) setPhase('FAILED');
    })().catch(() => undefined);
  }, [videoRef, live, collect, fitCalibration]);

  const points = phase === 'DONE' ? 2 : phase === 'BOTTOM' || phase === 'EVALUATING' ? 1 : 0;

  return {
    phase,
    points,
    running: phase === 'CAMERA' || phase === 'BOTTOM' || phase === 'EVALUATING',
    summary,
    countdownRef,
    start,
    /** 워커가 떴나 · 엔진이 없으면 사유 */
    engineReady: ready,
    engineError: error,
  };
}
