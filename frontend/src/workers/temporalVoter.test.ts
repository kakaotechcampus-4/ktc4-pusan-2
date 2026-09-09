import { describe, expect, it } from 'vitest';
import { TemporalVoter } from './temporalVoter';
import { compressToSegments } from '@/shared/lib/gazeSegments';
import type { FrameVerdict, ZoneDecision } from './gaze.contract';
import type { GazeZone } from '@/types/api';

const v = (zone: GazeZone, confidence = 0.9): FrameVerdict => ({ zone, confidence });

/** 판단 n개를 1초 안에 흩어 넣습니다 */
function pushMany(voter: TemporalVoter, verdict: FrameVerdict, n: number) {
  for (let i = 0; i < n; i++) voter.push(verdict, i * 10);
}

describe('1초 다수결 (vote-v1)', () => {
  it('판단 10개를 넣으면 1초 뒤 정확히 하나를 낸다', () => {
    const voter = new TemporalVoter();
    pushMany(voter, v('CAMERA'), 10);

    // 1초가 안 지났으면 아무것도 안 나온다
    expect(voter.decide(999)).toBeNull();

    const d = voter.decide(1000);
    expect(d).not.toBeNull();
    expect(d).toMatchObject({ tMs: 1000, zone: 'CAMERA', sampleCount: 10 });

    // 같은 1초에 두 번째는 없다 — 이게 "정확히 하나"의 뜻이다
    expect(voter.decide(1000)).toBeNull();
    expect(voter.decide(1500)).toBeNull();

    // 버퍼는 비워졌으므로 다음 1초는 표본 0이다
    expect(voter.decide(2000)).toMatchObject({ zone: 'UNCERTAIN', sampleCount: 0 });
  });

  it('표본이 MIN_SAMPLES 미만이면 UNCERTAIN', () => {
    const voter = new TemporalVoter();
    pushMany(voter, v('CAMERA'), TemporalVoter.MIN_SAMPLES - 1);

    expect(voter.decide(1000)).toMatchObject({
      zone: 'UNCERTAIN',
      confidence: 0,
      sampleCount: TemporalVoter.MIN_SAMPLES - 1,
    });

    // 딱 MIN_SAMPLES 면 판정이 나온다 (경계)
    const voter2 = new TemporalVoter();
    pushMany(voter2, v('BOTTOM'), TemporalVoter.MIN_SAMPLES);
    expect(voter2.decide(1000)).toMatchObject({ zone: 'BOTTOM' });
  });

  it('다수가 VOTE_THRESHOLD 미만이면 UNCERTAIN', () => {
    // 6:5 = 0.545 < 0.6 — 표본은 충분한데 갈렸다
    const split = new TemporalVoter();
    pushMany(split, v('CAMERA'), 6);
    pushMany(split, v('BOTTOM'), 5);
    const d = split.decide(1000);
    expect(d?.zone).toBe('UNCERTAIN');
    expect(d?.sampleCount).toBe(11);
    expect(d!.confidence).toBeLessThan(TemporalVoter.VOTE_THRESHOLD);

    // 7:3 = 0.7 >= 0.6 — 같은 표본 수인데 이번엔 판정이 선다
    const clear = new TemporalVoter();
    pushMany(clear, v('CAMERA'), 7);
    pushMany(clear, v('BOTTOM'), 3);
    expect(clear.decide(1000)).toMatchObject({ zone: 'CAMERA', confidence: 0.7 });
  });

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
