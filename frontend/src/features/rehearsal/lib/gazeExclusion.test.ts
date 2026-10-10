import { describe, expect, it } from 'vitest';
import { exclusionForDeviceError, exclusionForGazeError } from './gazeExclusion';

describe('발표 중 문제 — Take 시선을 통째로 뺄지, 기록만 멈출지', () => {
  it('카메라가 끊기면 제외하지 않는다 — 끊기기 전 기록은 살리고 빈 시간은 서버가 측정 못 함으로 채운다', () => {
    expect(exclusionForGazeError('CAMERA_LOST')).toBeNull();
    expect(exclusionForDeviceError('NOT_FOUND')).toBeNull();
    expect(exclusionForDeviceError('IN_USE')).toBeNull();
    expect(exclusionForDeviceError('UNKNOWN')).toBeNull();
  });

  it('카메라 권한을 거부했으면 그 이유로 제외한다', () => {
    expect(exclusionForDeviceError('PERMISSION_DENIED')).toBe('USER_DECLINED');
  });

  it('엔진이 못 뜨거나 죽었으면 지금처럼 제외한다', () => {
    expect(exclusionForGazeError('ENGINE_UNAVAILABLE')).toBe('ENGINE_UNAVAILABLE');
  });
});
