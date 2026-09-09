import { describe, expect, it } from 'vitest';
import { compressToSegments, summarize, validate } from './gazeSegments';
import type { ZoneDecision } from '@/workers/gaze.contract';

const d = (tMs: number, zone: ZoneDecision['zone']): ZoneDecision => ({
  tMs,
  zone,
  confidence: 0.9,
  sampleCount: 12,
});

describe('시선 구간 압축', () => {
  it('같은 zone이 이어지면 하나로 합친다', () => {
    const out = compressToSegments([d(0, 'CAMERA'), d(1000, 'CAMERA'), d(2000, 'BOTTOM')], 1000);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ startMs: 0, endMs: 2000, zone: 'CAMERA' });
    expect(out[1]).toMatchObject({ startMs: 2000, endMs: 3000, zone: 'BOTTOM' });
  });

  it('시간 합계가 관계식을 만족한다', () => {
    const segs = compressToSegments(
      [d(0, 'CAMERA'), d(1000, 'BOTTOM'), d(2000, 'UNCERTAIN')],
      1000,
    );
    const s = summarize(segs);
    expect(s.cameraMs + s.bottomMs).toBe(s.measuredMs);
    expect(s.measuredMs + s.uncertainMs).toBe(s.trackedMs);
  });

  it('발표 시간을 넘는 구간을 잡아낸다', () => {
    const segs = compressToSegments([d(0, 'CAMERA')], 1000);
    expect(validate(segs, 500)).not.toHaveLength(0);
    expect(validate(segs, 5000)).toHaveLength(0);
  });
});
