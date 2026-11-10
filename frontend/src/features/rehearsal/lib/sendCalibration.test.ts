import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { CALIBRATION_RETRY_DELAYS_MS, sendCalibration } from './sendCalibration';

const noWait = vi.fn((_ms: number) => Promise.resolve());

afterEach(() => {
  noWait.mockClear();
  vi.restoreAllMocks();
});

describe('발표 전 점검 결과 다시 보내기 (#1179)', () => {
  it('처음에 받으면 다시 보내지 않는다', async () => {
    const send = vi.fn().mockResolvedValue({});
    await expect(sendCalibration(send, noWait)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('연결 실패 · 서버 오류는 정한 간격으로 다시 보내고, 받으면 멈춘다', async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new ApiFailure(503, 'UNAVAILABLE'))
      .mockResolvedValue({});
    await expect(sendCalibration(send, noWait)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(3);
    expect(noWait.mock.calls.map(([ms]) => ms)).toEqual([...CALIBRATION_RETRY_DELAYS_MS]);
  });

  it('끝내 못 받으면 false — 처음 1번 + 다시 보내기 횟수만큼만 보낸다', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const send = vi.fn().mockRejectedValue(new ApiFailure(500, 'INTERNAL'));
    await expect(sendCalibration(send, noWait)).resolves.toBe(false);
    expect(send).toHaveBeenCalledTimes(1 + CALIBRATION_RETRY_DELAYS_MS.length);
  });

  it('다시 보내도 같은 4xx(이미 시작한 Take 등)는 다시 보내지 않는다', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const send = vi.fn().mockRejectedValue(new ApiFailure(409, 'TAKE_ALREADY_STARTED'));
    await expect(sendCalibration(send, noWait)).resolves.toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('시간 초과(408) · 너무 잦음(429)은 다시 보낸다', async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce(new ApiFailure(408, 'TIMEOUT'))
      .mockRejectedValueOnce(new ApiFailure(429, 'TOO_MANY'))
      .mockResolvedValue({});
    await expect(sendCalibration(send, noWait)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(3);
  });
});
