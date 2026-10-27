import { describe, expect, it } from 'vitest';
import { countdownParts, LAUNCH_AT, launchIsOpen, parseLaunchStatus } from './launch';

describe('출시 시각과 서버 공개 정책', () => {
  it('다른 타임존 표기도 동일한 공개 순간이다', () => {
    expect(Date.parse(LAUNCH_AT)).toBe(Date.parse('2026-10-30T05:00:00Z'));
  });

  it('직전·정각·직후와 하루 경계에서 음수 없이 계산한다', () => {
    expect(countdownParts(1)).toEqual([0, 0, 0, 1]);
    expect(countdownParts(0)).toEqual([0, 0, 0, 0]);
    expect(countdownParts(-1000)).toEqual([0, 0, 0, 0]);
    expect(countdownParts(90061000)).toEqual([1, 1, 1, 1]);
  });

  it('예정 시각 이후라도 서버가 닫혀 있으면 열지 않는다', () => {
    const data = { server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: false, receivedAt: 0 };
    expect(launchIsOpen(data)).toBe(false);
    expect(launchIsOpen({ ...data, is_open: true })).toBe(true);
    expect(launchIsOpen({ ...data, server_now: '2026-10-30T13:59:59+09:00', is_open: true })).toBe(
      false,
    );
    expect(launchIsOpen(undefined)).toBe(false);
  });

  it('잘못된 응답을 공개 상태로 받아들이지 않는다', () => {
    expect(() =>
      parseLaunchStatus({ server_now: 'invalid', launch_at: LAUNCH_AT, is_open: true }),
    ).toThrow();
    expect(() =>
      parseLaunchStatus({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: 'true' }),
    ).toThrow();
    expect(() => parseLaunchStatus(null)).toThrow();
  });
});
