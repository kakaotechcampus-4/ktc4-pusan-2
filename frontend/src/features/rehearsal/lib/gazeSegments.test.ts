import { describe, expect, it } from 'vitest';
import { compressToSegments, summarize, validate, zoneOf } from './gazeSegments';
import { makeConfig } from '@/vendor/gaze/engine/config';
import { GazeSlicer, sampleToDict } from '@/vendor/gaze/engine/evidence';
import type { GazeFrame, GazeSampleRecord } from '@/workers/gaze.contract';

const s = (t_ms: number, state: GazeSampleRecord['state']): GazeSampleRecord => ({
  t_ms,
  duration_ms: 1000,
  state,
  direction: state === 'OTHER' ? 'LEFT' : null,
  confidence: state === 'UNMEASURED' ? 0 : 0.9,
  reliability: 0.95,
  issues: [],
  frames: 8,
});

describe('엔진 상태 → 3구역', () => {
  it('화면과 대본은 BOTTOM, 판정 못 한 것은 모두 UNCERTAIN', () => {
    expect(zoneOf('CAMERA')).toBe('CAMERA');
    expect(zoneOf('SCREEN')).toBe('BOTTOM');
    expect(zoneOf('BOTTOM')).toBe('BOTTOM');
    expect(zoneOf('OTHER')).toBe('UNCERTAIN');
    expect(zoneOf('UNCERTAIN')).toBe('UNCERTAIN');
    expect(zoneOf('UNMEASURED')).toBe('UNCERTAIN');
  });
});

describe('시선 구간 압축', () => {
  it('같은 구역이 이어지면 하나로 합친다', () => {
    const out = compressToSegments([s(0, 'CAMERA'), s(1000, 'CAMERA'), s(2000, 'BOTTOM')]);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ startMs: 0, endMs: 2000, zone: 'CAMERA' });
    expect(out[1]).toMatchObject({ startMs: 2000, endMs: 3000, zone: 'BOTTOM' });
  });

  it('엔진 상태가 달라도 같은 구역으로 접히면 한 구간이다 — 화면→대본', () => {
    const out = compressToSegments([s(0, 'SCREEN'), s(1000, 'BOTTOM'), s(2000, 'OTHER')]);
    expect(out).toEqual([
      expect.objectContaining({ startMs: 0, endMs: 2000, zone: 'BOTTOM' }),
      expect.objectContaining({ startMs: 2000, endMs: 3000, zone: 'UNCERTAIN' }),
    ]);
  });

  it('틈이 있으면 같은 구역이어도 잇지 않는다 — 새로고침으로 격자가 새로 시작한 경우', () => {
    const out = compressToSegments([s(0, 'CAMERA'), s(1000, 'CAMERA'), s(5300, 'CAMERA')]);
    expect(out.map((g) => [g.startMs, g.endMs])).toEqual([
      [0, 2000],
      [5300, 6300],
    ]);
  });

  it('시간 합계가 관계식을 만족한다', () => {
    const segs = compressToSegments([s(0, 'CAMERA'), s(1000, 'BOTTOM'), s(2000, 'UNMEASURED')]);
    const t = summarize(segs);
    expect(t.cameraMs + t.bottomMs).toBe(t.measuredMs);
    expect(t.measuredMs + t.uncertainMs).toBe(t.trackedMs);
  });

  it('발표 시간을 넘는 구간을 잡아낸다', () => {
    const segs = compressToSegments([s(0, 'CAMERA')]);
    expect(validate(segs, 500)).not.toHaveLength(0);
    expect(validate(segs, 5000)).toHaveLength(0);
  });
});

/**
 * 엔진의 1초 묶기(`GazeSlicer`)와의 접점.
 *
 * 1초 묶기는 AI 엔진 몫이라 규칙 자체는 AI 테스트가 봅니다. 여기서 보는 것은
 * **엔진이 낸 기록이 FE 의 구간 압축에서 끊기지 않고 이어지는지**입니다 —
 * 압축은 `cur.endMs === s.t_ms` 일 때만 잇기 때문에, 엔진의 격자가 프레임 간격만큼
 * 밀리면 기록마다 별개 구간이 되어 압축이 아예 일어나지 않습니다.
 */
describe('엔진 1초 묶기 → 구간 압축', () => {
  const frame = (t_ms: number): GazeFrame => ({
    t_ms,
    state: 'CAMERA',
    direction: null,
    reliability: 1,
    issues: [],
  });

  it('기록이 격자에 붙어서 한 구간으로 이어진다', () => {
    for (const frameGap of [16, 80, 125]) {
      const slicer = new GazeSlicer(makeConfig().evidence);
      const samples: GazeSampleRecord[] = [];
      // 무대 시계는 0 이 아닌 데서 시작합니다 (엔진 준비 · 새로고침해 이어받은 Take)
      const start = 3_217;
      for (let t = start; t < start + 10_000; t += frameGap) {
        samples.push(...slicer.push(frame(t)).map(sampleToDict));
      }

      // ① 첫 프레임 시각에서 시작하고 간격이 **정확히** 1000ms 다.
      //    마지막 1초는 다음 프레임이 와야 닫히므로 10초 동안 9개가 나온다 (그 1초는 버린다)
      expect(samples, `frameGap=${frameGap}`).toHaveLength(9);
      expect(samples[0]!.t_ms, `frameGap=${frameGap}`).toBe(start);
      const gaps = samples.slice(1).map((x, i) => x.t_ms - samples[i]!.t_ms);
      expect(new Set(gaps), `frameGap=${frameGap}`).toEqual(new Set([1000]));

      // ② 그래야 같은 구역이 한 구간으로 압축된다 — 이게 이 테스트의 요점
      const segs = compressToSegments(samples);
      expect(segs, `frameGap=${frameGap}`).toEqual([
        expect.objectContaining({ startMs: start, endMs: start + 9_000, zone: 'CAMERA' }),
      ]);
    }
  });
});
