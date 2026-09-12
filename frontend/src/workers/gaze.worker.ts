/// <reference lib="webworker" />

import { DummyGazeClassifier } from './dummyClassifier';
import { ModelGazeClassifier } from './modelClassifier';
import { TemporalVoter } from './temporalVoter';
import type { GazeClassifier, GazeWorkerIn, GazeWorkerOut } from './gaze.contract';

/**
 * 시선 워커 — 프레임을 받아 1초 판정을 낸다.
 *
 * ┌ frame 도착
 * │   가짜 부하 N ms  (모델이 들어올 자리)
 * │   classifier.classify()  →  voter.push()
 * │   voter.decide()         →  1초마다 decision 전송
 * └ frameDone 전송 → 메인이 다음 프레임을 만든다 (백프레셔)
 *
 * ★ 이번 주에 만드는 건 "관"이고, 목적은 그 관의 비용을 재는 것이다.
 *   모델은 없다. 그 자리에 가짜 부하가 들어가 있다.
 *
 * MediaPipe 를 쓰지 않는다 — AI팀이 전처리까지 한다. 붙이면 버린다.
 */

const post = (msg: GazeWorkerOut, transfer?: Transferable[]) => {
  self.postMessage(msg, { transfer: transfer ?? [] });
};

// ── 가짜 부하 ──────────────────────────────────────────────────────────
//
// 워커 URL 의 쿼리에서 읽는다 — `new Worker(new URL('...?load=40', ...))`.
// 계약에 메시지를 추가하지 않으려고 이렇게 한다. 부하를 바꿀 때 워커를 새로 만들면
// **측정마다 상태가 초기화되어 앞 측정이 다음에 안 섞인다.**
const LOAD_MS = (() => {
  const raw = new URLSearchParams(self.location.search).get('load');
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : 0;
})();

/**
 * 정해진 ms 동안 스레드를 **점유한다.**
 *
 * setTimeout 이나 await 으로 하면 안 된다 — 실제 추론은 스레드를 점유하므로,
 * 점유하지 않는 대기로는 병목을 재현할 수 없다. 그런 부하로 잰 fps 는
 * 관의 비용이 아니라 그냥 타이머 정확도를 잰 숫자가 된다.
 */
function burn(ms: number): void {
  if (ms <= 0) return;
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    /* 의도적 점유 */
  }
}

// ── 상태 ───────────────────────────────────────────────────────────────

/**
 * 구현체를 쿼리에서 고릅니다 — `?impl=model`. 기본은 dummy.
 *
 * 부하와 같은 방식(URL 쿼리)인 이유는 계약에 메시지를 추가하지 않기 위해서입니다.
 * 그리고 이게 T12 판정 기준 B 의 시험대입니다 —
 * **dummy ↔ model 을 바꿔도 화면 코드는 한 줄도 안 바뀌어야 합니다.**
 * 실제로 안 바뀝니다: 화면은 useGazeWorker 만 부르고, 그 훅은 URL 만 다르게 만듭니다.
 */
const IMPL = new URLSearchParams(self.location.search).get('impl') === 'model' ? 'model' : 'dummy';

const classifier: GazeClassifier =
  IMPL === 'model' ? new ModelGazeClassifier() : new DummyGazeClassifier();
const voter = new TemporalVoter();

let running = false;
let lastFrameTMs = -1;

/** 실제로 끝낸 프레임 수 — 이게 진짜 숫자 */
let processed = 0;
/** 타임스탬프 역행·예외로 건너뛴 것 — 0이어야 정상 */
let droppedFrames = 0;
/** 프레임당 ms 누적 (perf 에 필드가 없어 콘솔로 나간다) */
let spentMs = 0;
let windowStart = 0;

function reportPerf(nowMs: number): void {
  const elapsed = nowMs - windowStart;
  if (elapsed < 1000) return;

  const avgFps = (processed * 1000) / elapsed;
  const msPerFrame = processed === 0 ? 0 : spentMs / processed;

  post({ type: 'perf', avgFps, droppedFrames });

  // perf 에 프레임당 ms 필드가 없다. 계약을 늘리지 않고 콘솔로 보낸다.
  console.info(
    `[gaze.worker] load=${LOAD_MS}ms  fps=${avgFps.toFixed(1)}  ` +
      `frame=${msPerFrame.toFixed(1)}ms  dropped=${droppedFrames}`,
  );

  processed = 0;
  spentMs = 0;
  windowStart = nowMs;
}

function handleFrame(bitmap: ImageBitmap, tMs: number): void {
  const started = performance.now();
  try {
    // 타임스탬프가 뒤로 가면 버린다. 1초 창 계산이 깨지기 때문이다.
    if (tMs <= lastFrameTMs) {
      droppedFrames++;
      return;
    }
    lastFrameTMs = tMs;

    // 모델이 들어올 자리. 지금은 스레드만 점유한다.
    burn(LOAD_MS);

    // ★ 비트맵을 그대로 넘긴다 (A안) — 전처리·얼굴검출은 분류기 안에서 한다.
    //   더미는 프레임을 보지 않고 시간으로 가짜 판정을 낸다.
    const verdict = classifier.classify(bitmap, tMs);
    if (verdict) voter.push(verdict, tMs);

    const decision = voter.decide(tMs);
    if (decision) post({ type: 'decision', decision });

    processed++;
    spentMs += performance.now() - started;
  } catch {
    // 프레임 하나가 터져도 파이프라인은 계속 돈다. 대신 세어서 보고한다.
    droppedFrames++;
  } finally {
    // ★ 반드시 닫는다. 오늘은 추론이 없어도 비트맵은 메모리를 잡는다.
    //   안 닫으면 몇 초 뒤 브라우저가 무거워지고, 그 상태에서 잰 fps 는 거짓이다.
    bitmap.close();

    // 백프레셔 — 이 신호를 받고서야 메인이 다음 프레임을 만든다.
    // finally 에 두는 이유: 예외가 나도 보내야 한다. 안 보내면 펌프가 영구히 멈춘다.
    post({ type: 'frameDone', tMs });
  }
}

self.onmessage = (e: MessageEvent<GazeWorkerIn>) => {
  const msg = e.data;

  switch (msg.type) {
    case 'init':
      // dummy 는 할 일이 없어 바로 ready. model 은 여기서 가중치를 확인하고,
      // 없으면 throw 한다 → error { ENGINE_UNAVAILABLE }.
      windowStart = performance.now();
      classifier
        .init()
        .then(() => {
          running = true;
          // 버전은 init 뒤에 읽는다 — 모델 파일 식별자가 여기서 정해진다.
          post({ type: 'ready', version: `${classifier.version}+vote-v1` });
        })
        .catch(() => {
          // ★ running 을 올리지 않는다. 프레임이 와도 처리하지 않고 깃발만 내려 준다.
          //   그래야 펌프가 멈추지 않고, 화면은 "측정 제외"로 계속 돌 수 있다.
          running = false;
          post({ type: 'error', reason: 'ENGINE_UNAVAILABLE' });
        });
      return;

    case 'fitCalibration': {
      // ★ 비트맵은 여기서 닫는다. 계약에 "호출부가 닫는다"고 적어 둔 그 호출부다.
      //   실패해도 닫아야 한다 — 안 닫으면 4초분 프레임이 GPU 메모리에 그대로 남는다.
      let ref = null;
      try {
        ref = classifier.fitCalibration(msg.camera, msg.bottom);
      } catch {
        ref = null;
      } finally {
        for (const b of msg.camera) b.close();
        for (const b of msg.bottom) b.close();
      }
      // null 이면 품질 미달 — 화면이 재시도를 안내한다.
      post({ type: 'calibrated', ref });
      return;
    }

    case 'calibrate':
      classifier.calibrate(msg.ref);
      return;

    case 'frame':
      if (!running) {
        // stop 이후 도착한 프레임. 비트맵만 놓고 깃발도 내려 준다.
        msg.bitmap.close();
        post({ type: 'frameDone', tMs: msg.tMs });
        return;
      }
      handleFrame(msg.bitmap, msg.tMs);
      reportPerf(performance.now());
      return;

    case 'stop':
      running = false;
      classifier.dispose();
      voter.reset();
      lastFrameTMs = -1;
      processed = 0;
      droppedFrames = 0;
      spentMs = 0;
      return;
  }
};
