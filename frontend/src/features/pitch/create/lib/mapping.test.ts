import { describe, expect, it } from 'vitest';
import { splitScript } from './mapping';

describe('대본 나누기 — 임시 규칙', () => {
  it('항상 슬라이드 장수만큼의 블록을 돌려준다', () => {
    expect(splitScript('가\n\n나', 5)).toHaveLength(5);
    expect(splitScript('', 3)).toEqual(['', '', '']);
  });

  it('문단이 충분하면 문단 단위로 나눈다', () => {
    expect(splitScript('가\n나\n\n다\n\n라', 3)).toEqual(['가\n나', '다', '라']);
  });

  /** 시안의 대본은 한 줄씩 띄어 쓴 것이 아니라 문장마다 한 줄입니다 */
  it('문단이 모자라면 줄 단위로 나눈다', () => {
    expect(splitScript('가\n나\n다', 3)).toEqual(['가', '나', '다']);
  });

  it('모자라면 뒤쪽 슬라이드가 빈 블록이다', () => {
    expect(splitScript('가\n나', 4)).toEqual(['가', '나', '', '']);
  });

  it('넘치면 남는 글을 마지막 블록에 이어 붙인다 — 글이 사라지지 않는다', () => {
    expect(splitScript('가\n나\n다\n라', 2)).toEqual(['가', '나\n다\n라']);
  });
});
