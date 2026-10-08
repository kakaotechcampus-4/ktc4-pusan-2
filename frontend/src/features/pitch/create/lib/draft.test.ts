import { describe, expect, it } from 'vitest';
import {
  CHOOSE_LATEST,
  countChars,
  estimateDurationMs,
  formatEstimate,
  toPracticeCombo,
  type PitchDraft,
} from './draft';

describe('대본 분량과 예상 시간', () => {
  it('공백은 세지 않는다', () => {
    expect(countChars('가 나\n다\t라')).toBe(4);
  });

  /**
   * 목업 06 의 "1,284자 · 예상 04:52" 를 그대로 맞춥니다.
   * 이 숫자가 어긋나면 상수(EST_CHARS_PER_MIN)가 바뀐 것이고,
   * 그때는 준비 화면의 estBasisWpm 과도 어긋납니다.
   */
  it('1,284자는 04:52 로 보인다 — 목업 06', () => {
    expect(formatEstimate(estimateDurationMs('가'.repeat(1_284)))).toBe('04:52');
  });

  it('빈 대본은 00:00', () => {
    expect(formatEstimate(estimateDurationMs(''))).toBe('00:00');
  });

  it('한 자리 초도 두 자리로 적는다', () => {
    expect(formatEstimate(5_000)).toBe('00:05');
  });
});

describe('장치 점검에 넘길 연습 조합 — BE Take 에 박히는 값', () => {
  const draft: PitchDraft = {
    title: '캡스톤',
    presentationDate: '',
    timeLimitSec: 300,
    lowerToleranceSec: 30,
    upperToleranceSec: 60,
    slides: [
      { version: 1, pageCount: 12, presentationVersionId: 'pv1' },
      { version: 2, pageCount: 12, presentationVersionId: 'pv2' },
    ],
    scripts: [
      {
        version: 3,
        text: '대본',
        slideVersion: 1,
        remote: { id: 'sv3', version: 2 },
        parse: { status: 'idle' },
        blocks: Array.from({ length: 12 }, () => '가'),
        segmented: true,
        saved: true,
      },
    ],
    criteria: [],
  };

  it('저장한 조합의 서버 id 와 목표 시간을 넘긴다 — 최신(V2)이 아니라 고른 V1', () => {
    expect(toPracticeCombo(draft, { ...CHOOSE_LATEST, slides: 1, script: 3 })).toEqual({
      title: '캡스톤',
      presentationVersionId: 'pv1',
      scriptVersionId: 'sv3',
      slideVersion: 1,
      scriptVersion: 3,
      goalTimeSec: 300,
    });
  });

  it('서버에 올리지 않은 대본이면 Take 를 만들 수 없다', () => {
    const local = { ...draft, scripts: [{ ...draft.scripts[0]!, remote: null }] };
    expect(toPracticeCombo(local, CHOOSE_LATEST)).toBeNull();
  });
});
