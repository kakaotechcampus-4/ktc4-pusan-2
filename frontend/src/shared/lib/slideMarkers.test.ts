import { describe, expect, it } from 'vitest';
import { markerNumber, scanSlideMarkers } from './slideMarkers';

describe('슬라이드 구분자 — AI script-parser 규칙', () => {
  it('"슬라이드 N" · "Slide N" · "N." · "N)" 을 줄 맨 앞에서만 찾는다', () => {
    expect(markerNumber('슬라이드 3')).toBe(3);
    expect(markerNumber('  Slide 2 - 도입')).toBe(2);
    expect(markerNumber('4. 결론')).toBe(4);
    expect(markerNumber('5) 마무리')).toBe(5);
    expect(markerNumber('오늘은 슬라이드 3 을 봅니다')).toBeNull();
  });

  it('빈 줄과 전환 표현은 구분자가 아니다', () => {
    expect(scanSlideMarkers('안녕하세요\n\n오늘은\n\n반갑습니다').numbers).toEqual([]);
    expect(markerNumber('첫째로')).toBeNull();
  });

  it('첫 구분자 앞의 제목 한 줄은 괜찮다 — AI 가 지우고 나눈다', () => {
    expect(scanSlideMarkers('피치코치 소개\n\n슬라이드 1\n가')).toEqual({
      numbers: [1],
      leadingText: false,
    });
  });

  it('첫 구분자 앞에 두 줄 이상 글이 있으면 알려 준다 — AI 는 그때 나누지 않는다', () => {
    expect(scanSlideMarkers('제목\n인사말\n슬라이드 1\n가')).toEqual({
      numbers: [1],
      leadingText: true,
    });
    expect(scanSlideMarkers('\n슬라이드 1\n가\n슬라이드 2\n나')).toEqual({
      numbers: [1, 2],
      leadingText: false,
    });
  });
});
