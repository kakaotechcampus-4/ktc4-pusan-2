// @vitest-environment jsdom
import { act, createElement, useLayoutEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { openStageDevices } from '../stage/openDevices';
import { hasLiveAudio, startGate } from '../stage/startGate';
import { useCameraStream } from './useCameraStream';

class FakeTrack extends EventTarget {
  readyState: MediaStreamTrackState = 'live';
  stop = vi.fn(() => {
    this.readyState = 'ended';
  });
  end() {
    this.readyState = 'ended';
    this.dispatchEvent(new Event('ended'));
  }
}

function fakeStream(camera = false) {
  const audio = new FakeTrack();
  const video = camera ? [new FakeTrack()] : [];
  const stream = {
    getTracks: () => [audio, ...video],
    getAudioTracks: () => [audio],
    getVideoTracks: () => video,
  } as unknown as MediaStream;
  return { stream, audio, video };
}

let root: Root;
let media: ReturnType<typeof useCameraStream>;
let devices: EventTarget;
const getUserMedia = vi.fn<(constraints: MediaStreamConstraints) => Promise<MediaStream>>();

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  getUserMedia.mockReset();
  devices = Object.assign(new EventTarget(), { getUserMedia });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: devices });
  function Probe() {
    const state = useCameraStream();
    useLayoutEffect(() => {
      media = state;
    });
    return null;
  }
  root = createRoot(document.createElement('div'));
  await act(async () => root.render(createElement(Probe)));
});

afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

const failure = (name: string) => new DOMException('', name);
const chosen = { videoDeviceId: 'camera', audioDeviceId: 'usb-mic' };

describe('카메라와 마이크 오류를 나눠 받기', () => {
  it('카메라 권한을 거부해도 마이크가 열리면 카메라 오류만 남긴다', async () => {
    const { stream } = fakeStream();
    getUserMedia.mockRejectedValueOnce(failure('NotAllowedError')).mockResolvedValueOnce(stream);
    await act(async () => {
      await media.request();
    });
    expect(media.error).toBe('PERMISSION_DENIED');
    await act(async () => {
      await media.requestMic('usb-mic');
    });
    expect(media.stream).toBe(stream);
    expect(media.cameraError).toBe('PERMISSION_DENIED');
    expect(media.micError).toBeNull();
    await act(async () => devices.dispatchEvent(new Event('devicechange')));
    expect(media.cameraError).toBe('PERMISSION_DENIED');
  });

  it('중간에 권한 창을 닫았어도 다음 요청에서 열리면 카메라 오류가 사라진다', async () => {
    const { stream } = fakeStream(true);
    let resolveCamera!: (stream: MediaStream) => void;
    getUserMedia
      .mockRejectedValueOnce(failure('NotAllowedError'))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveCamera = resolve)));
    let opening!: Promise<MediaStream | null>;
    await act(async () => {
      opening = openStageDevices(chosen, media);
      await Promise.resolve();
    });
    // 다음 요청을 기다리는 동안에는 앞 요청의 오류를 내보내지 않습니다.
    expect(media.cameraError).toBeNull();
    await act(async () => {
      resolveCamera(stream);
      await opening;
    });
    expect(media.stream).toBe(stream);
    expect(media.cameraError).toBeNull();
    expect(media.micError).toBeNull();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  it('마이크까지 실패하면 마이크 오류를 따로 돌려준다', async () => {
    getUserMedia
      .mockRejectedValueOnce(failure('NotAllowedError'))
      .mockRejectedValueOnce(failure('NotReadableError'));
    await act(async () => {
      await media.request();
      await media.requestMic('usb-mic');
    });
    expect(media.stream).toBeNull();
    expect(media.cameraError).toBe('PERMISSION_DENIED');
    expect(media.micError).toBe('IN_USE');
  });

  it.each(['NotAllowedError', 'NotReadableError'])(
    '카메라 오류(%s) 뒤 마이크가 끝나면 마이크 없음으로 시작을 막는다',
    async (name) => {
      const { stream, audio } = fakeStream();
      getUserMedia.mockRejectedValueOnce(failure(name)).mockResolvedValueOnce(stream);
      await act(async () => {
        await media.request();
        await media.requestMic('usb-mic');
      });
      const cameraError = media.cameraError;
      await act(async () => audio.end());
      expect(media.cameraError).toBe(cameraError);
      expect(media.micError).toBe('NOT_FOUND');
      expect(
        startGate({
          started: false,
          resumed: false,
          micLive: hasLiveAudio(media.stream),
          micError: media.micError,
          connected: true,
          startFailure: null,
          slow: false,
        }),
      ).toEqual({ kind: 'NO_MIC', error: 'NOT_FOUND' });
    },
  );

  it('카메라만 끝나면 마이크 오류로 옮기지 않고 언마운트에서 트랙을 놓는다', async () => {
    const { stream, audio, video } = fakeStream(true);
    getUserMedia.mockResolvedValueOnce(stream);
    await act(async () => {
      await media.request();
    });
    await act(async () => video[0].end());
    expect(media.cameraError).toBe('NOT_FOUND');
    expect(media.micError).toBeNull();
    await act(async () => root.unmount());
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video[0].stop).toHaveBeenCalledOnce();
  });
});

describe('고른 마이크를 기본 마이크로 바꾸는 조건', () => {
  it.each(['NotFoundError', 'OverconstrainedError'])(
    '%s 로 고른 마이크가 없으면 기본 마이크로 연다',
    async (name) => {
      const { stream } = fakeStream();
      getUserMedia.mockRejectedValueOnce(failure(name)).mockResolvedValueOnce(stream);
      await act(async () => {
        await media.requestMic('usb-mic');
      });
      expect(media.stream).toBe(stream);
      expect(media.micError).toBeNull();
      expect(getUserMedia.mock.calls.map(([c]) => c.audio)).toEqual([
        { echoCancellation: true, noiseSuppression: true, deviceId: { exact: 'usb-mic' } },
        { echoCancellation: true, noiseSuppression: true },
      ]);
    },
  );

  it.each(['NotReadableError', 'NotAllowedError', 'AbortError', 'UnknownError'])(
    '%s 일 때는 기본 마이크로 바꾸지 않는다',
    async (name) => {
      getUserMedia.mockRejectedValueOnce(failure(name));
      await act(async () => {
        await media.requestMic('usb-mic');
      });
      expect(media.stream).toBeNull();
      expect(media.micError).not.toBeNull();
      expect(getUserMedia).toHaveBeenCalledTimes(1);
    },
  );

  it('기본 마이크도 없으면 한 번만 요청하고 오류를 남긴다', async () => {
    getUserMedia.mockRejectedValueOnce(failure('NotFoundError'));
    await act(async () => {
      await media.requestMic();
    });
    expect(media.micError).toBe('NOT_FOUND');
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it('USB 마이크가 빠졌으면 기본 카메라와 기본 마이크를 함께 살린다', async () => {
    const { stream } = fakeStream(true);
    getUserMedia
      .mockRejectedValueOnce(failure('NotFoundError'))
      .mockRejectedValueOnce(failure('NotFoundError'))
      .mockResolvedValueOnce(stream);
    await act(async () => {
      await openStageDevices(chosen, media);
    });
    expect(media.stream).toBe(stream);
    expect(media.cameraError).toBeNull();
    expect(media.micError).toBeNull();
    expect(getUserMedia).toHaveBeenCalledTimes(3);
    expect(getUserMedia.mock.calls[2][0]).toMatchObject({
      video: { width: 640 },
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    expect(getUserMedia.mock.calls[2][0].audio).not.toHaveProperty('deviceId');
  });

  it('기본 카메라 재시도에서도 고른 마이크가 사용 중이면 내장 마이크로 바꾸지 않는다', async () => {
    getUserMedia.mockRejectedValue(failure('NotReadableError'));
    await act(async () => {
      await openStageDevices(chosen, media);
    });
    expect(media.stream).toBeNull();
    expect(media.micError).toBe('IN_USE');
    expect(getUserMedia).toHaveBeenCalledTimes(3);
    for (const [constraints] of getUserMedia.mock.calls) {
      expect(constraints.audio).toMatchObject({ deviceId: { exact: 'usb-mic' } });
    }
  });
});

describe('늦게 도착한 스트림 놓기 (#689)', () => {
  it('기다리는 사이 화면을 떠나면 늦게 온 스트림을 바로 놓는다', async () => {
    const { stream, audio, video } = fakeStream(true);
    let resolve!: (s: MediaStream) => void;
    getUserMedia.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    const opening = media.request();
    await act(async () => root.unmount());
    await act(async () => {
      resolve(stream);
      expect(await opening).toBeNull();
    });
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video[0].stop).toHaveBeenCalledOnce();
  });

  it('요청이 겹치면 앞 요청의 스트림을 놓고 마지막 것만 쓴다', async () => {
    const first = fakeStream(true);
    const second = fakeStream(true);
    const resolvers: ((s: MediaStream) => void)[] = [];
    getUserMedia.mockImplementation(() => new Promise((r) => resolvers.push(r)));
    const a = media.request({ videoDeviceId: 'a' });
    const b = media.request({ videoDeviceId: 'b' });
    await act(async () => {
      resolvers[0](first.stream);
      resolvers[1](second.stream);
      await Promise.all([a, b]);
    });
    expect(first.video[0].stop).toHaveBeenCalledOnce();
    expect(media.stream).toBe(second.stream);
    await act(async () => root.unmount());
    expect(second.video[0].stop).toHaveBeenCalledOnce();
  });
});
