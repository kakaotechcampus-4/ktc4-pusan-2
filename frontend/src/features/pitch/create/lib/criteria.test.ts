import { describe, expect, it } from 'vitest';
import { organizeCriteria, toAction } from './criteria';
import { MAX_CRITERIA } from './draft';

/** 시안 "평가기준 V2" 의 입력을 그대로 넣습니다 */
const SAMPLE = `시장 규모 숫자와 출처를 함께 말하고 싶어요.
도입 30초는 대본을 보지 않고 발표할래요.
채움말은 5회 이하로 줄이고, 발표를 멋지게 하고 싶어요.`;

describe('평가기준 정리 — 시안 평가기준 V2', () => {
  const { items, skipped } = organizeCriteria(SAMPLE);

  it('세 문장이 항목 셋이 된다', () => {
    expect(items).toEqual([
      '시장 규모 숫자와 출처를 함께 말하기',
      '도입 30초는 대본을 보지 않고 발표하기',
      '채움말은 5회 이하로 줄이기',
    ]);
  });

  it('"발표를 멋지게" 는 구체적이지 않아 반영하지 못한다', () => {
    expect(skipped).toHaveLength(1);
    expect(skipped[0]!.text).toBe('발표를 멋지게 하고 싶어요');
    expect(skipped[0]!.reason).toContain('구체적이지 않아');
  });

  it('빈 입력은 아무것도 만들지 않는다', () => {
    expect(organizeCriteria('  \n ')).toEqual({ items: [], skipped: [] });
  });

  it('목록 기호와 번호를 떼고 읽는다', () => {
    expect(organizeCriteria('- 결론을 한 문장으로 요약하기\n2) 출처를 말하기').items).toEqual([
      '결론을 한 문장으로 요약하기',
      '출처를 말하기',
    ]);
  });

  it(`${MAX_CRITERIA}개를 넘으면 뒤는 반영하지 않고 이유를 준다`, () => {
    const many = Array.from({ length: MAX_CRITERIA + 2 }, (_, i) => `${i + 1}회 말하기`).join('\n');
    const out = organizeCriteria(many);
    expect(out.items).toHaveLength(MAX_CRITERIA);
    expect(out.skipped).toHaveLength(2);
    expect(out.skipped[0]!.reason).toContain('최대');
  });
});

describe('말끝 다듬기', () => {
  it('모르는 말끝은 그대로 둔다', () => {
    expect(toAction('결론을 요약하기')).toBe('결론을 요약하기');
  });
});
