import { describe, expect, it } from 'vitest';
import { isScreenBody } from '@/shared/lib/chatScreen';
import { deviceScreen } from './chatScreen';
import { CALIBRATION_FAIL_MESSAGE } from './useGazeSetup';

const base = {
  live: true,
  hasStream: true,
  micOk: true,
  gazeStatus: 'DONE' as const,
  failReason: null,
};

describe('장치 점검 화면 상태 (12-4 F2)', () => {
  it('점검 훅 상태 그대로 — 계약 모양이고 장치 원본 · 식별자는 싣지 않는다', () => {
    const screen = deviceScreen(base);
    expect(screen).toEqual({
      name: 'device_check',
      camera: 'connected',
      mic: 'detected',
      gaze_setup: 'DONE',
      gaze_reason: null,
    });
    expect(isScreenBody(screen)).toBe(true);
  });

  it('마이크는 화면 줄과 같게 — 확인 · 재는 중 · 열기 전', () => {
    expect(deviceScreen({ ...base, micOk: false }).mic).toBe('checking');
    expect(deviceScreen({ ...base, micOk: false, hasStream: false, live: false })).toMatchObject({
      mic: 'disconnected',
      camera: 'disconnected',
    });
  });

  it('실패 사유는 화면에 보일 때(FAILED)만, 보이는 문구 그대로', () => {
    expect(
      deviceScreen({ ...base, gazeStatus: 'FAILED', failReason: 'NOT_ENOUGH_SAMPLES' }).gaze_reason,
    ).toBe(CALIBRATION_FAIL_MESSAGE.NOT_ENOUGH_SAMPLES);
    expect(
      deviceScreen({ ...base, gazeStatus: 'RUNNING', failReason: 'NOT_ENOUGH_SAMPLES' })
        .gaze_reason,
    ).toBeNull();
  });
});
