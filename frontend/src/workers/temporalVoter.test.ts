import { describe, expect, it } from 'vitest';
import { TemporalVoter } from './temporalVoter';
import type { FrameVerdict } from './gaze.contract';
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
   * 프레임 시각은 무대 시계라 0 이 아닌 데서 시작합니다 (엔진 준비 · 새로고침해 이어받은 Take).
   * 첫 프레임에서 바로 판정하면 표본 1장짜리 UNCERTAIN 이 첫 1초를 차지합니다.
   */
  it('첫 프레임 시각에서 1초를 채운 뒤에 처음 판정한다', () => {
    const voter = new TemporalVoter();
    for (let t = 0; t < 1000; t += 100) {
      voter.push(v('CAMERA'), 42_000 + t);
      expect(voter.decide(42_000 + t)).toBeNull();
    }
    expect(voter.decide(43_000)).toMatchObject({ tMs: 43_000, zone: 'CAMERA', sampleCount: 10 });
  });

  /** reset() 뒤의 첫 프레임은 무대 시계라 큰 값입니다. 0 에서 다시 세면 지난 시각의 판정을 쏟아냅니다 */
  it('reset() 뒤에는 다음 첫 프레임에서 창을 새로 연다', () => {
    const voter = new TemporalVoter();
    voter.push(v('CAMERA'), 10_000);
    voter.reset();

    voter.push(v('CAMERA'), 120_000);
    expect(voter.decide(120_000)).toBeNull();
    expect(voter.decide(121_000)).toMatchObject({ tMs: 121_000 });
  });
});
