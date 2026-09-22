import { FRAME_MS, SAMPLES_PER_FRAME, SAMPLE_RATE, encodeAudioFrame, floatToInt16 } from './pcm';

/**
 * 마이크 → 16kHz Int16 프레임. **STT 로 보낼 것만 만듭니다** — 보내지는 않습니다.
 *
 * 원본 보관(recorder.ts)과 **같은 스트림에서 따로** 돕니다. 한쪽이 죽어도 다른 쪽은
 * 그대로입니다. 원본이 먼저이고 이쪽은 덤이라는 것이 이 프로젝트의 전제입니다.
 *
 * ── 왜 AudioContext 를 16kHz 로 만드나 ──────────────────────────────
 * 브라우저가 리샘플링을 해 줍니다. 에일리어싱 방지 필터까지 포함해서요. 직접 줄이면
 * 44.1kHz 마이크(16kHz 의 정수 배가 아닙니다)에서 경우가 갈리고, 잘못 줄이면 에러
 * 없이 잡음 전사가 나옵니다.
 *
 * 음량 계량기(level.ts)는 기본 샘플레이트 컨텍스트를 씁니다 — 목적이 다르므로
 * 컨텍스트를 공유하지 않습니다.
 */

/**
 * 워크릿은 `public/worklets/` 의 평범한 JS 입니다 — 번들러를 태우지 않습니다
 * (CLAUDE.md 7번). 그래서 import 가 아니라 실행 시점 URL 입니다.
 */
const WORKLET_URL = '/worklets/pcm.js';

export interface PcmFrame {
  /** 1 부터 올라갑니다. 줄어들면 BE 가 조용히 버립니다 */
  seq: number;
  /** Take 시작 기준 ms. 시선 tMs · 녹음 offsetMs 와 같은 시계입니다 */
  offsetMs: number;
  /** 그대로 WebSocket 에 실을 수 있는 바이너리 프레임 */
  frame: ArrayBuffer;
}

export interface PcmCapture {
  /** 지금까지 만든 프레임 수 */
  readonly frameCount: number;
  stop(): Promise<void>;
}

/** 워크릿이 넘겨주는 것 */
interface WorkletMessage {
  pcm: Float32Array;
  /** 오디오 스트림 시작 기준 ms — Take 시작 기준이 아닙니다 */
  startMs: number;
}

/**
 * @param stream        getUserMedia 가 준 스트림
 * @param startedAtPerf 발표 시작 시각(`performance.now()`). 녹음과 **같은 값**을 넘기세요 —
 *                      두 기록을 나중에 겹쳐 보려면 기준이 같아야 합니다
 * @param onFrame       100ms 마다 한 번. 여기서 await 하지 마세요 — 다음 프레임이 밀립니다
 */
export async function startPcmCapture(
  stream: MediaStream,
  startedAtPerf: number,
  onFrame: (frame: PcmFrame) => void,
): Promise<PcmCapture | null> {
  if (stream.getAudioTracks().length === 0) return null;

  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  // 제스처 밖에서 만들면 suspended 로 시작하고 **에러 없이** 오디오가 안 흐릅니다.
  // level.ts 와 같은 이유로 한 번 시도합니다.
  if (ctx.state === 'suspended') await ctx.resume().catch(() => undefined);

  await ctx.audioWorklet.addModule(WORKLET_URL);

  /**
   * 오디오 시계가 0 이던 순간을 `performance.now()` 눈금으로 옮긴 값.
   *
   * ★ 워크릿이 주는 `startMs` 는 **AudioContext 시계**(`ctx.currentTime` 과 같은 것)
   *   이고, 우리가 맞춰야 할 것은 발표 시계입니다. 둘 사이의 차이를 여기서 한 번만
   *   재 두면, 프레임마다 `performance.now()` 를 찍지 않아도 됩니다.
   *
   *   프레임이 도착한 시각으로 재면 안 됩니다 — 도착 시점에는 그 100ms 가 이미 다
   *   지나간 뒤라 프레임 전체가 한 칸씩 밀리고, 메시지 전달이 늦은 만큼 더 밀립니다.
   *   실측으로 첫 프레임이 181ms 에서 시작했습니다.
   */
  const ctxEpochPerf = performance.now() - ctx.currentTime * 1000;

  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-frame', {
    numberOfInputs: 1,
    numberOfOutputs: 0,
  });
  // ★ destination 에 잇지 않습니다. 이으면 자기 목소리가 스피커로 나가 하울링이 됩니다.
  source.connect(node);

  let seq = 0;
  const int16 = new Int16Array(SAMPLES_PER_FRAME);

  node.port.onmessage = (e: MessageEvent<WorkletMessage>) => {
    const { pcm, startMs } = e.data;

    // 오디오 시계 → 발표 시계. 프레임이 **시작된** 시각입니다 (도착한 시각이 아닙니다).
    // 음수는 캡처가 발표보다 먼저 시작된 경우인데, BE 헤더가 uint32 라 0 으로 붙입니다.
    const offsetMs = Math.max(0, Math.round(ctxEpochPerf + startMs - startedAtPerf));

    seq += 1;
    onFrame({
      seq,
      offsetMs,
      frame: encodeAudioFrame(seq, offsetMs, floatToInt16(pcm, int16)),
    });
  };

  return {
    get frameCount() {
      return seq;
    },
    async stop() {
      node.port.onmessage = null;
      source.disconnect();
      node.disconnect();
      await ctx.close().catch(() => undefined);
    },
  };
}

/** 한 프레임이 덮는 시간. 호출부가 갭을 판단할 때 씁니다 */
export const PCM_FRAME_MS = FRAME_MS;
