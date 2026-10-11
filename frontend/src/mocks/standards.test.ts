import { describe, expect, it } from 'vitest';
import { splitStandards } from './standards';

describe('평가기준 나누기 목', () => {
  it('줄과 문장 단위로 나누고 머리표를 뗀다', () => {
    expect(
      splitStandards('1. 시장 규모 숫자를 말한다.\n- 도입부 30초는 대본 없이. 군더더기 5회 이하'),
    ).toEqual(['시장 규모 숫자를 말한다', '도입부 30초는 대본 없이', '군더더기 5회 이하']);
  });

  it('빈 줄과 한 글자짜리는 버린다', () => {
    expect(splitStandards('\n\n음\n  \n결론을 먼저 말한다')).toEqual(['결론을 먼저 말한다']);
  });
});
