import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import {
  CALIBRATION_RETRY_DELAYS_MS,
  CALIBRATION_TIMEOUT_MS,
  sendCalibration,
} from './sendCalibration';

const noWait = vi.fn((_ms: number) => Promise.resolve());
const options = { wait: noWait };

/** 답 없이 걸리는 요청 — `fetch` 처럼 signal 이 끊기면 AbortError 로 끝납니다 */
const hanging = (signals: AbortSignal[]) => (signal: AbortSignal) => {
  signals.push(signal);
  return new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () =>
      reject(new DOMException('The operation was aborted.', 'AbortError')),
    );
  });
};

afterEach(() => {
  noWait.mockClear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('발표 전 점검 결과 다시 보내기 (#1179)', () => {
  it('처음에 받으면 다시 보내지 않는다', async () => {
    const send = vi.fn().mockResolvedValue({});
    await expect(sendCalibration(send, options)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('연결 실패 · 서버 오류는 정한 간격으로 다시 보내고, 받으면 멈춘다', async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new ApiFailure(503, 'UNAVAILABLE'))
      .mockResolvedValue({});
    await expect(sendCalibration(send, options)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(3);
    expect(noWait.mock.calls.map(([ms]) => ms)).toEqual([...CALIBRATION_RETRY_DELAYS_MS]);
  });

  it('끝내 못 받으면 false — 처음 1번 + 다시 보내기 횟수만큼만 보낸다', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const send = vi.fn().mockRejectedValue(new ApiFailure(500, 'INTERNAL'));
    await expect(sendCalibration(send, options)).resolves.toBe(false);
    expect(send).toHaveBeenCalledTimes(1 + CALIBRATION_RETRY_DELAYS_MS.length);
  });

  it('다시 보내도 같은 4xx(이미 시작한 Take 등)는 다시 보내지 않는다', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const send = vi.fn().mockRejectedValue(new ApiFailure(409, 'TAKE_ALREADY_STARTED'));
    await expect(sendCalibration(send, options)).resolves.toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('시간 초과(408) · 너무 잦음(429)은 다시 보낸다', async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce(new ApiFailure(408, 'TIMEOUT'))
      .mockRejectedValueOnce(new ApiFailure(429, 'TOO_MANY'))
      .mockResolvedValue({});
    await expect(sendCalibration(send, options)).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(3);
  });
});

// #1184 리뷰 — 요청이 답 없이 걸려도 시작이 멈추지 않게 합니다
describe('보낼 때마다 제한 시간 (#1184)', () => {
  it('답 없이 걸린 요청은 제한 시간에 실제로 끊고 다시 보내며, 끝내 못 받으면 false 로 끝난다', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const signals: AbortSignal[] = [];
    const done = sendCalibration(hanging(signals));
    // 시도 3번의 제한 시간과 그 사이 대기를 모두 흘립니다
    await vi.advanceTimersByTimeAsync(
      CALIBRATION_TIMEOUT_MS * 3 + CALIBRATION_RETRY_DELAYS_MS.reduce((a, b) => a + b, 0),
    );
    await expect(done).resolves.toBe(false);
    expect(signals).toHaveLength(1 + CALIBRATION_RETRY_DELAYS_MS.length);
    // 기다리기만 그만둔 것이 아니라 요청마다 끊었습니다
    expect(signals.every((s) => s.aborted)).toBe(true);
  });

  it('걸렸던 요청을 끊고 다시 보낸 요청이 받으면 true 다', async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const first = hanging(signals);
    const send = vi
      .fn<(signal: AbortSignal) => Promise<unknown>>()
      .mockImplementationOnce(first)
      .mockResolvedValue({});
    const done = sendCalibration(send);
    await vi.advanceTimersByTimeAsync(CALIBRATION_TIMEOUT_MS + CALIBRATION_RETRY_DELAYS_MS[0]);
    await expect(done).resolves.toBe(true);
    expect(send).toHaveBeenCalledTimes(2);
    expect(signals[0]!.aborted).toBe(true);
  });

  it('제때 받은 요청은 끊지 않는다', async () => {
    vi.useFakeTimers();
    let seen: AbortSignal | null = null;
    await expect(
      sendCalibration((signal) => {
        seen = signal;
        return Promise.resolve({});
      }),
    ).resolves.toBe(true);
    await vi.advanceTimersByTimeAsync(CALIBRATION_TIMEOUT_MS * 2);
    expect(seen!.aborted).toBe(false);
  });
});
