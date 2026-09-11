import { describe, expect, it } from 'vitest';
import { compressToSegments, summarize, validate } from './gazeSegments';
import { TemporalVoter } from '@/workers/temporalVoter';
import type { FrameVerdict, ZoneDecision } from '@/workers/gaze.contract';
import type { GazeZone } from '@/types/api';

/** 프레임 하나의 판단. 아래 TemporalVoter 접점 테스트에서 씁니다 */
const v = (zone: GazeZone, confidence = 0.9): FrameVerdict => ({ zone, confidence });

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

/**
 * TemporalVoter 와의 접점 검증.
 *
 * workers/temporalVoter.test.ts 에 있던 것을 여기로 옮겼습니다 —
 * 검증 대상이 다수결 자체가 아니라 **다수결 결과가 구간으로 압축되는지**라
 * gazeSegments 쪽 테스트입니다. 워커 테스트가 features/ 를 참조하는
 * 역방향 의존도 함께 사라집니다.
 */
describe('TemporalVoter 판정 → 구간 압축', () => {
  /**
   * ★ gazeSegments 와의 경계. 이게 깨지면 조용히 무너진다.
   *
   * decide() 는 프레임이 도착할 때만 불린다. 판정 시각을 nowMs 로 맞추면
   * 프레임 간격만큼 밀리고(60fps→1008ms, 12fps→1040ms) 그 밀림이 누적된다.
   * compressToSegments 는 `cur.endMs === d.tMs` 일 때만 병합하므로,
   * 밀리면 판정마다 별개 구간이 되어 압축이 아예 일어나지 않는다.
   */
  it('판정 시각이 격자에 붙어서 구간이 이어진다', () => {
    for (const frameGap of [16, 80]) {
      const voter = new TemporalVoter();
      const decisions: ZoneDecision[] = [];
      // 10초 동안 프레임을 흘려보낸다. 매 프레임 같은 zone 을 충분히 넣는다.
      for (let t = 0; t <= 10_000; t += frameGap) {
        voter.push(v('CAMERA'), t);
        const d = voter.decide(t);
        if (d) decisions.push(d);
      }
      // 초당 하나 — 첫 판정이 첫 프레임 시각에 앉으므로 10초 창에 9개다.
      // (밀림 유무로는 개수가 갈리지 않는다. 아래 두 단정이 밀림을 잡는다.)
      expect(decisions.length, `frameGap=${frameGap}`).toBeGreaterThanOrEqual(9);
      // ① 판정 간격이 **정확히** 1000ms. 밀리면 1008·1040 이 섞인다.
      const gaps = decisions.slice(1).map((d, i) => d.tMs - decisions[i]!.tMs);
      expect(new Set(gaps), `frameGap=${frameGap}`).toEqual(new Set([1000]));
      // ② 그래야 같은 zone 이 한 구간으로 압축된다 — 이게 이 테스트의 요점.
      //    밀리면 판정 수만큼 구간이 생긴다.
      const segs = compressToSegments(decisions, TemporalVoter.INTERVAL_MS);
      expect(segs, `frameGap=${frameGap}`).toHaveLength(1);
      expect(segs[0]).toMatchObject({ zone: 'CAMERA' });
      expect(segs[0]!.endMs - segs[0]!.startMs).toBe(decisions.length * 1000);
    }
  });
});
