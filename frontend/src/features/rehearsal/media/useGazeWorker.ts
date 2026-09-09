import { useCallback, useEffect, useRef, useState } from 'react';
import type { GazeWorkerIn, GazeWorkerOut, ZoneDecision } from '@/workers/gaze.contract';

export interface GazePerf {
  /** 실제로 끝낸 프레임 수 기준. 보낸 수가 아니다 — 이게 진짜 숫자 */
  avgFps: number;
  /** 타임스탬프 역행·예외로 건너뛴 것. 0이어야 정상 */
  droppedFrames: number;
  /**
   * 프레임 하나의 왕복 시간 — postMessage 부터 frameDone 까지, 메인에서 실측.
   *
   * 워커가 보내는 perf 에는 이 필드가 없다(계약을 늘리지 않으려고).
   * 백프레셔로 떠 있는 프레임이 항상 1장 이하라서, 이 왕복이 곧 파이프라인의
   * 한 프레임 주기다. 모델 예산을 여기서 읽는다.
   */
  msPerFrame: number;
}

export type GazeWorkerError = 'ENGINE_UNAVAILABLE' | 'CAMERA_LOST';

/**
 * ★ `?worker&url` 로 받는다. 이유가 있다.
 *
 * `new URL('...gaze.worker.ts', import.meta.url)` 을 모듈 상수로 끌어올리면
 * Vite 가 **워커로 인식하지 못한다.** 그 패턴은 `new Worker()` 인자 자리에
 * 직접 있을 때만 워커로 처리되고, 끌어올리면 그냥 애셋 URL 이 된다.
 * 그러면 컴파일 안 된 .ts 원본이 dist 에 복사되고 —
 * import 문과 타입 주석이 남은 채 확장자가 .ts 라 **브라우저가 실행을 거부한다.**
 * dev 에서는 Vite 가 요청마다 변환해 주므로 **프로덕션에서만 터진다.**
 *
 * `?worker&url` 은 **번들된** 워커 청크의 URL 을 준다. 거기에 런타임으로
 * 쿼리를 붙이면 청크는 하나로 유지되고 부하 값만 달라진다.
 */
import gazeWorkerUrl from '@/workers/gaze.worker?worker&url';

/** 분류기 구현체. 화면은 이 값만 바꾸고, 나머지 코드는 그대로다 (T12 판정 기준 B) */
export type GazeImpl = 'dummy' | 'model';

function makeWorker(loadMs: number, impl: GazeImpl): Worker {
  // 부하와 구현체를 워커 URL 의 쿼리로 넘긴다 — 계약에 메시지를 추가하지 않기 위해서다.
  // 워커 안에서 self.location.search 로 읽는다.
  const url = new URL(gazeWorkerUrl, self.location.href);
  url.searchParams.set('load', String(loadMs));
  url.searchParams.set('impl', impl);
  return new Worker(url, { type: 'module' });
}

interface WorkerState {
  /** 이 상태가 어느 워커에서 나온 것인지. 부하나 구현체가 바뀌면 되돌리는 기준이 된다 */
  loadMs: number;
  impl: GazeImpl;
  ready: boolean;
  /**
   * 워커가 ready 로 보낸 엔진 버전. **Take 에 영구 고정되는 값입니다** —
   * 서버는 시선을 재계산할 수 없으므로 어느 엔진이 낸 숫자인지가 유일한 근거입니다.
   * 엔진이 못 떴으면 null 이고, 그때는 payload 가 'unavailable' 로 채웁니다.
   */
  engineVersion: string | null;
  error: GazeWorkerError | null;
  /** 1초에 한 번만 온다. 그 주기의 렌더는 이 프로젝트가 허용하는 범위다 */
  perf: GazePerf | null;
}

const freshState = (loadMs: number, impl: GazeImpl): WorkerState => ({
  loadMs,
  impl,
  ready: false,
  engineVersion: null,
  error: null,
  perf: null,
});

/**
 * 시선 워커를 만들고, 비디오에서 프레임을 계속 워커로 보낸다.
 *
 * ── 백프레셔가 이 훅의 존재 이유다 ──────────────────────────────────
 *
 * postMessage 는 우체통에 편지를 넣는 것이다. 상대가 읽었는지 안 물어본다.
 * 초당 60장 넣고 24장 처리하면 36장이 쌓인다. 그러면 —
 *
 *   메모리 : 프레임 하나가 GPU 메모리를 잡는다. 쌓이면 찬다
 *   측정   : 보낸 수로 세면 60, 처리한 수로 세면 24.
 *            **이번 주에 재려는 숫자가 거짓이 된다**
 *
 * 그래서 깃발 하나를 둔다 —
 *   프레임 보냄 → 깃발 올림 → 워커가 frameDone → 깃발 내림 → 다음 프레임
 *
 * 깃발이 올라가 있으면 createImageBitmap 을 **아예 부르지 않는다.**
 * 그러면 우체통에 항상 1장 이하이고, fps 가 자연히 실제 처리 속도에 수렴한다.
 *
 * @param onDecision 1초 판정 콜백. **React 상태로 올리지 않는다** — 받는 쪽이 처리한다
 * @param loadMs     가짜 부하. 바뀌면 워커를 새로 만든다 (앞 측정이 다음에 안 섞이게)
 */
export function useGazeWorker(
  onDecision: (d: ZoneDecision) => void,
  loadMs: number,
  impl: GazeImpl = 'dummy',
) {
  const [state, setState] = useState<WorkerState>(() => freshState(loadMs, impl));

  // 부하가 바뀌면 워커를 새로 만든다. 그 표시를 **렌더 중에** 되돌린다 —
  // 이게 React 가 권하는 "입력이 바뀔 때 상태 초기화" 방식이다.
  // 효과 안에서 setState 하면 렌더가 한 번 더 돌고, 그 사이 한 프레임 동안
  // 이전 부하의 fps 가 새 부하의 숫자처럼 보인다.
  if (state.loadMs !== loadMs || state.impl !== impl) setState(freshState(loadMs, impl));

  const { ready, engineVersion, error, perf } = state;

  const workerRef = useRef<Worker | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const rafRef = useRef(0);
  /** ★ 백프레셔 깃발 — 워커에 프레임이 떠 있는 동안 true */
  const inFlightRef = useRef(false);
  /** 펌프 시작 시각. tMs 는 이 시각 기준 경과 시간이다 */
  const pumpStartRef = useRef(0);
  /** 왕복 시간 실측 — 보낸 시각과 frameDone 도착 시각의 차이를 1초 창으로 평균낸다 */
  const sentAtRef = useRef(0);
  const rttSumRef = useRef(0);
  const rttCountRef = useRef(0);
  /** 카메라 소실을 한 번만 보고하기 위한 표시 */
  const cameraLostRef = useRef(false);

  // 콜백을 ref 에 담는다 — 콜백이 바뀔 때마다 워커를 다시 만들면 안 된다.
  const onDecisionRef = useRef(onDecision);
  useEffect(() => {
    onDecisionRef.current = onDecision;
  }, [onDecision]);

  // ── 워커 생명주기. loadMs 가 바뀌면 새로 만든다 ────────────────────
  useEffect(() => {
    const worker = makeWorker(loadMs, impl);
    workerRef.current = worker;
    inFlightRef.current = false;
    rttSumRef.current = 0;
    rttCountRef.current = 0;

    worker.onmessage = (e: MessageEvent<GazeWorkerOut>) => {
      const msg = e.data;
      switch (msg.type) {
        case 'ready':
          setState((s) =>
            s.loadMs === loadMs && s.impl === impl
              ? { ...s, ready: true, engineVersion: msg.version }
              : s,
          );
          return;
        case 'frameDone':
          // 깃발 내림. 다음 tick 이 프레임을 만든다.
          rttSumRef.current += performance.now() - sentAtRef.current;
          rttCountRef.current += 1;
          inFlightRef.current = false;
          return;
        case 'decision':
          onDecisionRef.current(msg.decision);
          return;
        case 'perf': {
          const n = rttCountRef.current;
          const next: GazePerf = {
            avgFps: msg.avgFps,
            droppedFrames: msg.droppedFrames,
            msPerFrame: n === 0 ? 0 : rttSumRef.current / n,
          };
          rttSumRef.current = 0;
          rttCountRef.current = 0;
          // 이 워커가 아직 현재 부하의 워커일 때만 반영한다. 종료 중인 워커의
          // 마지막 perf 가 새 부하의 숫자로 보이면 측정표가 오염된다.
          setState((s) => (s.loadMs === loadMs && s.impl === impl ? { ...s, perf: next } : s));
          return;
        }
        case 'error':
          setState((s) =>
            s.loadMs === loadMs && s.impl === impl ? { ...s, error: msg.reason } : s,
          );
          return;
      }
    };

    // 워커가 터지면 측정 불가로 본다. 조용히 멈추는 것이 최악이다.
    worker.onerror = () =>
      setState((st) =>
        st.loadMs === loadMs && st.impl === impl ? { ...st, error: 'ENGINE_UNAVAILABLE' } : st,
      );

    const init: GazeWorkerIn = { type: 'init' };
    worker.postMessage(init);

    return () => {
      // ★ 정리를 빠뜨리면 HMR 마다 워커가 쌓이고 fps 가 점점 떨어진다.
      //
      // 여기서 펌프(rAF)는 건드리지 않는다. 이 정리는 **부하를 바꿀 때마다** 돌고,
      // 그때 펌프를 죽이면 카메라는 켜져 있는데 프레임이 안 흘러서
      // 사용자가 [정지] → [카메라 시작] 을 다시 눌러야 한다. 측정 흐름이 끊긴다.
      // 펌프는 workerRef 를 매 tick 읽으므로, 워커가 없는 사이에는 그냥 쉰다.
      const stop: GazeWorkerIn = { type: 'stop' };
      worker.postMessage(stop);
      worker.terminate();
      workerRef.current = null;
      // 죽은 워커의 frameDone 은 오지 않는다. 깃발을 내려야 새 워커에서 펌프가 이어진다.
      inFlightRef.current = false;
    };
  }, [loadMs, impl]);

  // 펌프는 언마운트에서만 끈다. 위 정리와 합치면 부하를 바꿀 때 같이 죽는다.
  useEffect(
    () => () => {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    },
    [],
  );

  // ── 프레임 펌프 ────────────────────────────────────────────────────
  // 이름 붙인 함수 표현식(`loop`)으로 둔다 — 화살표 함수로 두면 초기화 중인
  // 자기 자신을 참조하게 되고, 그 참조가 언제 유효한지가 렌더 순서에 달린다.
  const tick = useCallback(function loop() {
    rafRef.current = requestAnimationFrame(loop);

    // 깃발이 올라가 있으면 여기서 끝. createImageBitmap 도 부르지 않는다 —
    // 만들어 두고 안 보내면 그것도 메모리다.
    if (inFlightRef.current) return;

    const worker = workerRef.current;
    const video = videoRef.current;
    if (!worker || !video) return;

    // ── 발표 중 카메라 소실 ──────────────────────────────────────────
    //
    // 이걸 보지 않으면 조용히 헛돈다: 트랙이 끝나면 createImageBitmap 이
    // 계속 실패하고, catch 가 깃발을 내리고, 다음 tick 이 또 시도한다.
    // 화면에는 아무 표시가 없고 fps 만 0 으로 떨어진다.
    //
    // 소실은 측정 제외 사유이지만 **발표는 계속 갑니다** —
    // 타이머·키보드·녹음은 그대로 돌고 시선만 조용해집니다 (CLAUDE.md 4번).
    const track = (video.srcObject as MediaStream | null)?.getVideoTracks()[0];
    if (!track || track.readyState === 'ended') {
      if (!cameraLostRef.current) {
        cameraLostRef.current = true;
        setState((s) => ({ ...s, error: 'CAMERA_LOST' }));
        // 펌프를 세운다. 방금 위에서 예약한 프레임을 취소하는 것이다.
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      return;
    }

    // HAVE_CURRENT_DATA 미만이면 아직 그릴 프레임이 없다
    if (video.readyState < 2) return;

    inFlightRef.current = true;
    const tMs = Math.round(performance.now() - pumpStartRef.current);

    createImageBitmap(video)
      .then((bitmap) => {
        const w = workerRef.current;
        if (!w) {
          // 그 사이에 워커가 사라졌다. 비트맵을 놓고 깃발을 내린다.
          bitmap.close();
          inFlightRef.current = false;
          return;
        }
        const frame: GazeWorkerIn = { type: 'frame', bitmap, tMs };
        sentAtRef.current = performance.now();
        // transfer — 복사가 아니라 소유권 이동
        w.postMessage(frame, [bitmap]);
      })
      .catch(() => {
        // 비디오가 아직 준비 안 됐거나 트랙이 끊겼다. 깃발을 내려 펌프가 멈추지 않게.
        inFlightRef.current = false;
      });
  }, []);

  const startPump = useCallback(
    (video: HTMLVideoElement) => {
      videoRef.current = video;
      pumpStartRef.current = performance.now();
      cancelAnimationFrame(rafRef.current);
      inFlightRef.current = false;
      cameraLostRef.current = false;
      rafRef.current = requestAnimationFrame(tick);
    },
    [tick],
  );

  const stopPump = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;
    videoRef.current = null;
    inFlightRef.current = false;
  }, []);

  return { ready, engineVersion, error, perf, startPump, stopPump };
}
