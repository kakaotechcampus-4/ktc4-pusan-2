import { describe, expect, it } from 'vitest';
import type { PitchResources } from '@/types/pitch';
import { basisBadge, basisPitches, staleTexts, UNKNOWN_STALE_TEXT } from './chatBasis';

const resources: PitchResources = {
  pitch_id: 'p-1',
  presentation_versions: [],
  script_versions: [
    { id: 'sv-1', version: 1 },
    { id: 'sv-2', version: 2 },
  ],
  evaluation_versions: [],
};

const pitch = (over: Record<string, unknown> = {}) =>
  basisPitches({
    pitches: [
      {
        pitch_id: 'p-1',
        script_version_id: 'sv-2',
        presentation_version_id: 'pv-2',
        standard_version: 4,
        takes: [{ take_id: 't-5', take_number: 5 }],
        ...over,
      },
    ],
  })[0]!;

describe('근거 배지 (12-4 F5)', () => {
  it('"Take 5 · 대본 V2 · 기준 v4 기준" — 대본 이름은 그 피치의 버전 목록에서', () => {
    expect(basisBadge(pitch(), resources)).toBe('Take 5 · 대본 V2 · 기준 v4 기준');
  });

  it('여러 Take 는 번호순으로 한 번씩', () => {
    const p = pitch({
      takes: [
        { take_id: 't-5', take_number: 5 },
        { take_id: 't-4', take_number: 4 },
        { take_id: 't-5', take_number: 5 },
      ],
    });
    expect(basisBadge(p, resources)).toBe('Take 4, 5 · 대본 V2 · 기준 v4 기준');
  });

  it('버전 이름을 찾지 못하면 그 칸은 빼고 ID 를 보이지 않는다', () => {
    expect(basisBadge(pitch(), undefined)).toBe('Take 5 · 기준 v4 기준');
    expect(basisBadge(pitch({ script_version_id: 'sv-없음' }), resources)).toBe(
      'Take 5 · 기준 v4 기준',
    );
  });

  it('보일 것이 없으면 null', () => {
    expect(
      basisBadge(pitch({ takes: [], script_version_id: null, standard_version: null }), resources),
    ).toBeNull();
  });

  it('모양이 다른 근거 표시는 피치가 없는 것으로 — 깨진 Take 는 뺀다', () => {
    expect(basisPitches(null)).toEqual([]);
    expect(basisPitches({ pitches: 'x' })).toEqual([]);
    expect(basisPitches({ pitches: [{ takes: [] }] })).toEqual([]);
    expect(basisPitches({ pitches: [{ pitch_id: 'p-1', takes: 'x' }] })).toEqual([]);
    expect(pitch({ takes: [{ take_id: 't' }, { take_id: 't-2', take_number: 2 }] }).takes).toEqual([
      { take_id: 't-2', take_number: 2 },
    ]);
  });
});

describe('최신 아님 (6-8)', () => {
  it('사유마다 문구, 같은 문구는 한 번', () => {
    expect(staleTexts(['NEW_TAKE', 'RULE_VERSION_CHANGED', 'RESULT_UNAVAILABLE'])).toEqual([
      '이 답 뒤에 새 Take가 있어요',
      '자료 또는 판정 규칙이 바뀌어 현재 기준과 다를 수 있어요',
    ]);
  });

  it('모르는 사유는 "일부 기준이 바뀌었어요"', () => {
    expect(staleTexts(['SOMETHING_NEW', 7])).toEqual([UNKNOWN_STALE_TEXT]);
    expect(staleTexts(null)).toEqual([]);
  });
});
