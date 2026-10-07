import { describe, expect, it } from 'vitest';
import { CONFIG_HASH } from '@/vendor/gaze/engine/config';
import { fitsCurrentEngine } from './calibrationModel';

describe('저장된 보정이 지금 엔진 설정과 맞는가', () => {
  it('설정 해시가 같으면 쓴다', () => {
    expect(fitsCurrentEngine({ schema: 'x', configHash: CONFIG_HASH })).toBe(true);
  });

  it('버전 문자열은 같아도 설정 해시가 다르면 버린다 — 엔진 calibrate() 는 이걸 거르지 않는다', () => {
    expect(fitsCurrentEngine({ schema: 'x', configHash: 'old-config' })).toBe(false);
  });

  it('해시가 없는 모델(더미 · 옛 형식)은 버린다', () => {
    expect(fitsCurrentEngine({ kind: 'dummy' })).toBe(false);
    expect(fitsCurrentEngine(null)).toBe(false);
    expect(fitsCurrentEngine('model')).toBe(false);
  });
});
