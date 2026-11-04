import { beforeEach, describe, expect, it } from 'vitest';
import type { ZoneReference } from '@/workers/gaze.contract';
import { forgetZoneRef, keepZoneRef, takeZoneRef } from './zoneRefMemory';

const ref: ZoneReference = { quality: 'GOOD', metrics: null, model: { kind: 'test' } };

beforeEach(() => forgetZoneRef());

describe('시선 기준은 탭 메모리에만 둔다', () => {
  it('같은 배치 · 같은 엔진이면 장치 점검이 둔 기준을 리허설이 꺼낸다', () => {
    keepZoneRef('sig|stage:bottom-script-v2', ref, 'engine-1');
    expect(takeZoneRef('sig|stage:bottom-script-v2', 'engine-1')).toBe(ref);
  });

  it('이전 무대 배치에서 잡은 기준은 키가 일치해도 쓰지 않는다', () => {
    keepZoneRef('sig', ref, 'engine-1');
    expect(takeZoneRef('sig', 'engine-1')).toBeNull();
  });

  it('배치나 엔진이 다르면 꺼내지 않는다', () => {
    keepZoneRef('sig|stage:bottom-script-v2', ref, 'engine-1');
    expect(takeZoneRef('other|stage:bottom-script-v2', 'engine-1')).toBeNull();
    expect(takeZoneRef('sig|stage:bottom-script-v2', 'engine-2')).toBeNull();
  });

  it('버리면 없고, 새로 잡으면 덮어쓴다', () => {
    keepZoneRef('sig|stage:bottom-script-v2', ref, 'engine-1');
    forgetZoneRef();
    expect(takeZoneRef('sig|stage:bottom-script-v2', 'engine-1')).toBeNull();

    const next: ZoneReference = { ...ref, quality: 'FAIR' };
    keepZoneRef('sig|stage:bottom-script-v2', ref, 'engine-1');
    keepZoneRef('sig|stage:bottom-script-v2', next, 'engine-1');
    expect(takeZoneRef('sig|stage:bottom-script-v2', 'engine-1')).toBe(next);
  });
});
