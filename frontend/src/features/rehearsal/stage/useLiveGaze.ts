import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { useGazeWorker } from '../media/useGazeWorker';
import { appendGazeDecision, markGazeExcluded } from '../lib/db';
import { noteWriteFailure } from '../lib/writeFailures';
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
 */
export function useLiveGaze({
  stream,
  videoRef,
  stageRef,
  clientSessionId,
  enabled,
  onExcluded,
}: {
  stream: MediaStream | null;
  videoRef: RefObject<HTMLVideoElement>;
  stageRef: RefObject<HTMLDivElement>;
  clientSessionId: string | null;
  /** 시선 측정을 제외한 Take면 false — 워커를 아예 띄우지 않습니다 */
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

  const { ready, engineVersion, error, perf, startPump, stopPump } = useGazeWorker(
    onDecision,
    0,
    // 실모델은 /models 에 가중치가 들어오는 날 'model'로 바뀝니다 (I-03).
    // 그때 이 파일에서 바뀌는 건 이 한 글자뿐입니다 — 그게 계약 파일을 따로 둔 이유입니다.
    'dummy',
  );

  useEffect(() => {
    if (!enabled) return;
    const video = videoRef.current;
    if (!video || !stream) return;

    startPump(video);
    return () => stopPump();
  }, [enabled, stream, videoRef, startPump, stopPump]);

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

  return { ready, engineVersion, error, perf, bottomRatio };
}
