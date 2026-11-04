import { describe, expect, it } from 'vitest';
import { markerNumber, scanSlideMarkers } from './slideMarkers';

describe('슬라이드 구분자 — AI script-parser 규칙', () => {
  it.each(['[1]', '[슬라이드 1]', '슬라이드 1:'])('%s 형식을 줄 앞에서 인식한다', (marker) => {
    expect(markerNumber(marker)).toBe(1);
    expect(markerNumber(`  ${marker} 본문`)).toBe(1);
    expect(markerNumber(`본문 중간 ${marker}`)).toBeNull();
    expect(scanSlideMarkers(`${marker}\n첫 장\n${marker.replace('1', '2')}\n둘째 장`)).toEqual({
      numbers: [1, 2],
      leadingText: false,
    });
  });

  it('닫히지 않은 대괄호와 숫자가 아닌 표기는 인식하지 않는다', () => {
    for (const marker of ['[1', '[슬라이드 1', '[N]', '[슬라이드 N]']) {
      expect(markerNumber(marker)).toBeNull();
    }
  });

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
