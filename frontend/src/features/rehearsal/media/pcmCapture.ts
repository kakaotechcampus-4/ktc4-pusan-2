import { STT_SAMPLE_RATE } from '../lib/sttProtocol';
import type { Ms } from '@/types/api';

/**
 * 마이크 → 16kHz Int16 100ms 프레임. **음성이 서버로 가는 유일한 경로**입니다.
 *
 * 로컬 녹음은 두지 않습니다 (CLAUDE.md 4번). 이 파일이 실패하면 말하기 분석이
 * 빠질 뿐, 시선과 1단 코치는 계속 돕니다.
 *
 * `MediaRecorder` 를 쓰지 않는 이유 — 그게 뱉는 webm/opus 는 스트리밍 STT 에 못 씁니다.
 *
 * ── 음량 미터(level.ts)와도 왜 따로인가 ────────────────────────────
 *
 * 미터는 장치 기본 레이트의 AnalyserNode 가 필요하고 STT 는 16kHz 가 필요합니다.
 * `AudioContext` 의 sampleRate 는 만든 뒤에 못 바꾸므로 한 컨텍스트로 둘 다 할 수
 * 없습니다. 컨텍스트 두 개의 비용을 치르고 각자 제 레이트를 씁니다.
 */

/** 워클릿 파일 경로. 번들러를 안 태우므로 빌드 뒤에도 이 이름 그대로입니다 */
const WORKLET_URL = `${import.meta.env.BASE_URL}worklets/pcm-framer.js`;

export type PcmCaptureError =
  /** 스트림에 오디오 트랙이 없습니다 (카메라만 허용한 경우) */
  | 'NO_AUDIO_TRACK'
  /** AudioWorklet 을 모르는 브라우저입니다 */
  | 'UNSUPPORTED'
  /** 16kHz 로 열리지 않았습니다 — 아래 주석 참고 */
  | 'RATE_UNSUPPORTED'
  | 'WORKLET_FAILED';

export const PCM_CAPTURE_ERROR_MESSAGE: Record<PcmCaptureError, string> = {
  NO_AUDIO_TRACK: '마이크가 없어 말하기 분석을 켜지 못했어요. 발표는 계속하세요.',
  UNSUPPORTED: '이 브라우저는 말하기 분석을 지원하지 않아요. 발표는 계속하세요.',
  RATE_UNSUPPORTED: '이 브라우저에서 말하기 분석을 켜지 못했어요. 발표는 계속하세요.',
  WORKLET_FAILED: '말하기 분석을 켜지 못했어요. 발표는 계속하세요.',
};

export interface PcmFrame {
  /** Int16 LE. 16kHz 에서 100ms = 3,200 바이트입니다 */
  pcm: ArrayBuffer;
  /** Take 시작 = 0 기준의 프레임 시작 시각 */
  offsetMs: Ms;
}

export interface PcmCapture {
  readonly sampleRate: number;
  /** `suspended` 면 소리가 흐르지 않습니다 — 화면이 이유를 말해 줘야 합니다 */
  readonly state: AudioContextState;
  stop(): Promise<void>;
}

export interface StartPcmCaptureResult {
  capture: PcmCapture | null;
  error: PcmCaptureError | null;
}

/**
 * 캡처를 시작합니다. 실패해도 **던지지 않습니다** — 부르는 쪽은 발표 화면이고,
 * 여기서 예외가 올라가면 무대가 통째로 죽습니다. 결과로 알립니다.
 *
 * @param elapsedMs Take 경과 시간을 읽는 시계. 오디오 시계의 0 이 Take 의 몇 ms 인지를
 *   **AudioContext 를 만든 직후에** 한 번 찍어 둡니다. 워클릿을 받아 오는 데 드는
 *   시간(수십~수백 ms)이 여기 포함되어야 전사 시각이 실제 발화 시각과 맞습니다.
 *   스트림이 바뀌어 캡처를 다시 걸어도 `offset_ms` 가 0 으로 돌아가지 않습니다 —
 *   서버는 시각이 역행하는 프레임을 버립니다.
 */
export async function startPcmCapture(
  stream: MediaStream,
  {
    elapsedMs = () => 0,
    onFrame,
  }: {
    elapsedMs?: () => Ms;
    onFrame: (frame: PcmFrame) => void;
  },
): Promise<StartPcmCaptureResult> {
  if (stream.getAudioTracks().length === 0) return { capture: null, error: 'NO_AUDIO_TRACK' };
  if (typeof AudioWorkletNode === 'undefined') return { capture: null, error: 'UNSUPPORTED' };

  const context = new AudioContext({ sampleRate: STT_SAMPLE_RATE });

  /**
   * 오디오 시계의 0 이 Take 의 몇 ms 인가. **여기서 찍어야** 합니다 —
   * 아래 `addModule` 은 네트워크를 타고, 첫 프레임이 나오기까지 걸린 그 시간만큼
   * 전사가 통째로 앞당겨집니다. 워클릿이 주는 `startFrame` 은 컨텍스트를 만든
   * 시점부터 세므로, 이 값에 더하면 준비 시간이 저절로 들어갑니다.
   */
  const audioClockStartedAtMs = elapsedMs();

  /**
   * 브라우저가 16kHz 를 거절하면 장치 기본 레이트로 열립니다. 그대로 보내면
   * 서버는 바이트 수로 시간을 세기 때문에 **전사 시각이 통째로 어긋납니다.**
   * 조용히 틀린 리포트보다 STT 없는 리포트가 낫습니다.
   */
  if (context.sampleRate !== STT_SAMPLE_RATE) {
    await context.close().catch(() => undefined);
    return { capture: null, error: 'RATE_UNSUPPORTED' };
  }

  // getUserMedia 를 이미 통과한 시점이라 대개 바로 흐릅니다. 안 되면 state 로 드러냅니다
  if (context.state === 'suspended') await context.resume().catch(() => undefined);

  let node: AudioWorkletNode;
  try {
    await context.audioWorklet.addModule(WORKLET_URL);
    node = new AudioWorkletNode(context, 'pcm-framer', { numberOfOutputs: 1 });
  } catch {
    await context.close().catch(() => undefined);
    return { capture: null, error: 'WORKLET_FAILED' };
  }

  const source = context.createMediaStreamSource(stream);
  // 출력이 어딘가로 이어지지 않으면 process() 를 안 돌리는 브라우저가 있습니다.
  // gain 0 이라 스피커로는 아무 소리도 나가지 않습니다 (하울링 방지)
  const mute = context.createGain();
  mute.gain.value = 0;
  source.connect(node).connect(mute).connect(context.destination);

  node.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; startFrame: number }>) => {
    const { pcm, startFrame } = event.data;

    // 오디오 시계로 잽니다. 메인 스레드가 밀려도 이 값은 밀리지 않습니다
    const audioElapsedMs = Math.round((startFrame / context.sampleRate) * 1000);
    onFrame({ pcm, offsetMs: audioClockStartedAtMs + audioElapsedMs });
  };

  return {
    capture: {
      sampleRate: context.sampleRate,
      state: context.state,
      async stop() {
        node.port.onmessage = null;
        source.disconnect();
        node.disconnect();
        mute.disconnect();
        await context.close().catch(() => undefined);
      },
    },
    error: null,
  };
}
