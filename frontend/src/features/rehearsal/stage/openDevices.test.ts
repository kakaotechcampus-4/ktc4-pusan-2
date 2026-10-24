import { describe, expect, it } from 'vitest';
import type { DeviceChoice } from '../media/useCameraStream';
import { openStageDevices } from './openDevices';

const stream = { id: 'stream' } as unknown as MediaStream;

/** 부른 순서를 적고, 정해 둔 차례에만 스트림을 돌려줍니다 */
function fakeOpen(opensAt: number | null) {
  const calls: string[] = [];
  const answer = () => (calls.length - 1 === opensAt ? stream : null);
  return {
    calls,
    open: {
      request: (choice?: DeviceChoice) => {
        calls.push(`request ${choice?.videoDeviceId ?? '-'} ${choice?.audioDeviceId ?? '-'}`);
        return Promise.resolve(answer());
      },
      requestMic: (audioDeviceId?: string) => {
        calls.push(`requestMic ${audioDeviceId ?? '-'}`);
        return Promise.resolve(answer());
      },
    },
  };
}

const chosen = { videoDeviceId: 'cam', audioDeviceId: 'mic' };

describe('발표 화면의 장치 열기', () => {
  it('점검에서 쓴 장치가 열리면 거기서 멈춘다', async () => {
    const { calls, open } = fakeOpen(0);
    await expect(openStageDevices(chosen, open)).resolves.toBe(stream);
    expect(calls).toEqual(['request cam mic']);
  });

  it('고른 장치를 못 열면 기본 장치로 연다', async () => {
    const { calls, open } = fakeOpen(1);
    await expect(openStageDevices(chosen, open)).resolves.toBe(stream);
    expect(calls).toEqual(['request cam mic', 'request - -']);
  });

  it('카메라를 못 열어도 고른 마이크만 열어 시작한다', async () => {
    const { calls, open } = fakeOpen(2);
    await expect(openStageDevices(chosen, open)).resolves.toBe(stream);
    expect(calls).toEqual(['request cam mic', 'request - -', 'requestMic mic']);
  });

  it('고른 장치가 없으면 기본 장치를 두 번 열지 않는다', async () => {
    const { calls, open } = fakeOpen(1);
    await expect(openStageDevices({}, open)).resolves.toBe(stream);
    expect(calls).toEqual(['request - -', 'requestMic -']);
  });

  it('마이크까지 못 열면 null 이다', async () => {
    const { calls, open } = fakeOpen(null);
    await expect(openStageDevices(chosen, open)).resolves.toBeNull();
    expect(calls).toHaveLength(3);
  });
});
