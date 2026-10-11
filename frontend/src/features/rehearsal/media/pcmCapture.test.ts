import { afterEach, describe, expect, it, vi } from 'vitest';
import { STT_SAMPLE_RATE } from '../lib/sttProtocol';
import { startPcmCapture } from './pcmCapture';

/**
 * WebAudio 를 흉내 냅니다. 여기서 보려는 것은 소리가 아니라 **시각**입니다 —
 * 워클릿을 받아 오는 동안 흐른 시간이 offset 에 들어가는지, 16kHz 가 아니면
 * 보내지 않는지. 둘 다 틀려도 화면에는 아무 표시가 안 나서 눈으로는 못 잡습니다.
 */

type FramePort = {
  onmessage: ((event: { data: { pcm: ArrayBuffer; startFrame: number } }) => void) | null;
};

function stubWebAudio({
  sampleRate = STT_SAMPLE_RATE,
  onAddModule,
  startSuspended = false,
}: {
  sampleRate?: number;
  onAddModule?: () => void;
  /** 제스처 없이 만든 경우 — 멈춘 채로 시작하고, 제스처 안에서만 resume() 이 통합니다 */
  startSuspended?: boolean;
} = {}): FramePort {
  const port: FramePort = { onmessage: null };
  const passThrough = <T>(node: T) => node;

  class FakeAudioContext extends EventTarget {
    sampleRate = sampleRate;
    state: AudioContextState = startSuspended ? 'suspended' : 'running';
    currentTime = 0;
    destination = {};
    audioWorklet = {
      addModule: () => {
        onAddModule?.();
        return Promise.resolve();
      },
    };

    createMediaStreamSource = () => ({ connect: passThrough, disconnect: () => undefined });
    createGain = () => ({ gain: { value: 1 }, connect: passThrough, disconnect: () => undefined });
    resume = () => {
      // 명세대로 거절하지 않고 제스처가 올 때까지 대기합니다 (끝나지 않는 promise)
      if (startSuspended && !inGesture) return new Promise<void>(() => undefined);
      this.state = 'running';
      this.dispatchEvent(new Event('statechange'));
      return Promise.resolve();
    };
    close = () => Promise.resolve();
  }

  class FakeAudioWorkletNode {
    port = port;
    connect = passThrough;
    disconnect = () => undefined;
  }

  vi.stubGlobal('AudioContext', FakeAudioContext);
  vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode);

  return port;
}

const micStream = { getAudioTracks: () => [{}] } as unknown as MediaStream;

/** 지금 사용자 제스처 안인가 (가짜 resume() 이 봅니다) */
let inGesture = false;

afterEach(() => vi.unstubAllGlobals());

describe('오디오 캡처', () => {
  it('워클릿을 받아 오는 동안 흐른 시간이 offset 에 들어간다', async () => {
    const setupMs = 300;
    let takeElapsedMs = 1_000;

    const port = stubWebAudio({ onAddModule: () => (takeElapsedMs += setupMs) });
    const offsets: number[] = [];

    const { capture, error } = await startPcmCapture(micStream, {
      elapsedMs: () => takeElapsedMs,
      onFrame: ({ offsetMs }) => offsets.push(offsetMs),
    });

    expect(error).toBeNull();

    // 첫 프레임은 준비가 끝난 직후, 즉 오디오 시계로 300ms 지점에서 나옵니다
    port.onmessage?.({
      data: { pcm: new ArrayBuffer(3_200), startFrame: (STT_SAMPLE_RATE * setupMs) / 1000 },
    });

    // 준비 시간을 빼먹으면 1,000 이 되고 전사가 통째로 300ms 앞당겨집니다
    expect(offsets).toEqual([1_300]);

    await capture?.stop();
  });

  it('16kHz 로 열리지 않으면 캡처하지 않는다 — 조용히 틀린 시각을 보내느니 안 보낸다', async () => {
    stubWebAudio({ sampleRate: 48_000 });

    const { capture, error } = await startPcmCapture(micStream, { onFrame: () => undefined });

    expect(error).toBe('RATE_UNSUPPORTED');
    expect(capture).toBeNull();
  });

  it('오디오 트랙이 없으면 바로 알린다', async () => {
    stubWebAudio();

    const { error } = await startPcmCapture(
      { getAudioTracks: () => [] } as unknown as MediaStream,
      {
        onFrame: () => undefined,
      },
    );

    expect(error).toBe('NO_AUDIO_TRACK');
  });

  /**
   * 새로고침한 리허설 화면은 제스처가 없어 멈춘 채로 시작합니다. 첫 키 입력(→ 로 슬라이드 넘기기)
   * 에서 풀려야 하고, 멈춰 있던 동안은 오디오 시계도 멈추므로 기준점을 그때 다시 잡아야 합니다.
   */
  it('멈춘 채로 시작하면 첫 키 입력에서 풀리고, 풀린 시각을 기준으로 offset 을 잰다', async () => {
    const target = new EventTarget();
    vi.stubGlobal('window', target);
    let takeElapsedMs = 5_000;
    const port = stubWebAudio({ startSuspended: true });
    const offsets: number[] = [];

    const { capture } = await startPcmCapture(micStream, {
      elapsedMs: () => takeElapsedMs,
      onFrame: ({ offsetMs }) => offsets.push(offsetMs),
    });
    expect(capture?.state).toBe('suspended');

    // 4초 동안 멈춰 있다가 → 키를 누릅니다
    takeElapsedMs = 9_000;
    inGesture = true;
    target.dispatchEvent(new Event('keydown'));
    inGesture = false;
    await Promise.resolve();
    expect(capture?.state).toBe('running');

    // 풀리고 100ms 뒤의 프레임. 기준점을 그대로 두면 5,100 이 되어 전사가 4초 앞당겨집니다
    port.onmessage?.({ data: { pcm: new ArrayBuffer(3_200), startFrame: STT_SAMPLE_RATE / 10 } });
    expect(offsets).toEqual([9_100]);

    await capture?.stop();
  });
});
