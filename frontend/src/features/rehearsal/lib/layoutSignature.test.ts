import { describe, expect, it } from 'vitest';
import { makeLayoutSignature, type LayoutInputs } from './layoutSignature';

const base: LayoutInputs = {
  deviceId: 'b1946ac92492d2347c6235b4d2611184',
  videoWidth: 1280,
  videoHeight: 720,
  screenWidth: 1920,
  screenHeight: 1080,
  devicePixelRatio: 1.25,
};

describe('layoutSignature', () => {
  it('같은 입력이면 같은 키다', () => {
    expect(makeLayoutSignature(base)).toBe(makeLayoutSignature({ ...base }));
  });

  it('deviceId 원문을 담지 않는다 — 서버로 가는 값이다', () => {
    const sig = makeLayoutSignature(base);
    expect(sig).not.toContain(base.deviceId);
    expect(sig).toMatch(/^cam:[0-9a-f]{8}\|v1280x720\|s1920x1080@1\.25$/);
  });

  it('카메라·해상도·화면 중 하나라도 다르면 다른 키다', () => {
    const sig = makeLayoutSignature(base);
    expect(makeLayoutSignature({ ...base, deviceId: 'other' })).not.toBe(sig);
    expect(makeLayoutSignature({ ...base, videoWidth: 640, videoHeight: 480 })).not.toBe(sig);
    expect(makeLayoutSignature({ ...base, devicePixelRatio: 1.5 })).not.toBe(sig);
  });

  it('배율의 부동소수 오차는 같은 키로 본다', () => {
    expect(makeLayoutSignature({ ...base, devicePixelRatio: 1.2500001 })).toBe(
      makeLayoutSignature(base),
    );
  });

  it('deviceId 를 모르면 unknown 으로 둔다', () => {
    expect(makeLayoutSignature({ ...base, deviceId: '' })).toMatch(/^cam:unknown\|/);
  });
});
