import { describe, expect, it } from 'vitest';
import { isLegacyMarker, markerNumber, scanSlideMarkers } from './slideMarkers';

/**
 * ★ FE · AI 공통 입력 목록 — AI `find_slides`(`tests/unit/script_parser/test_splitter.py`)도
 * 같은 목록으로 같은 번호를 내야 합니다. 한쪽을 고치면 다른 쪽 목록도 함께 고칩니다.
 * 값은 "구분자로 인식하는 번호", 구분자가 아니면 null.
 */
const LINE_CASES: [string, number | null][] = [
  // 인식하는 세 형식
  ['[1]', 1],
  ['[1] 안녕하세요', 1],
  ['  [2]', 2],
  ['\t[3] 들여쓰기', 3],
  ['[슬라이드 1]', 1],
  ['[슬라이드   4] 공백 여럿', 4],
  ['슬라이드 1:', 1],
  ['슬라이드 2: 도입', 2],
  ['  슬라이드 12: 들여쓰기', 12],
  ['[9999]', 9999],
  ['슬라이드 0:', 0], // 줄은 구분자지만 번호 0 이라 대본 전체가 fail (아래 SCRIPT_CASES)
  // 구분자가 아닌 표기 — 예전 FE 는 세던 것들
  ['슬라이드 1', null],
  ['슬라이드1:', null],
  ['슬라이드 1 :', null],
  ['슬라이드 1.', null],
  ['슬라이드 1 - 도입', null],
  ['Slide 1', null],
  ['slide 1:', null],
  ['Slide 1: 도입', null],
  ['1.', null],
  ['1) 도입', null],
  ['3.5%p 늘었습니다', null],
  ['2024. 올해는', null],
  // 처음부터 아니던 표기
  ['[Slide 1]', null],
  ['[슬라이드1]', null],
  ['[ 1 ]', null],
  ['# 슬라이드 1', null],
  ['[1', null],
  ['[슬라이드 1', null],
  ['[N]', null],
  ['[슬라이드 N]', null],
  ['[10000]', null], // 5자리 이상은 본문
  ['슬라이드 10000:', null],
  ['본문 중간 [1]', null],
  ['오늘은 슬라이드 3: 을 봅니다', null],
  ['첫째로', null],
  ['', null],
];

/** 대본 전체: AI 는 번호가 0 · 겹침 · 거꾸로면 fail, 구분자가 없어도 fail */
const SCRIPT_CASES: {
  name: string;
  script: string;
  status: 'success' | 'fail';
  numbers: number[];
}[] = [
  { name: '정상', script: '[1]\n가\n[2]\n나', status: 'success', numbers: [1, 2] },
  {
    name: '형식 섞기',
    script: '[1] 가\n슬라이드 2: 나\n[슬라이드 3]\n다',
    status: 'success',
    numbers: [1, 2, 3],
  },
  { name: '번호 건너뜀', script: '[1]\n가\n[3]\n나', status: 'success', numbers: [1, 3] },
  { name: '번호 0', script: '[0]\n가\n[1]\n나', status: 'fail', numbers: [0, 1] },
  { name: '번호 겹침', script: '[1]\n가\n[1]\n나', status: 'fail', numbers: [1, 1] },
  { name: '번호 거꾸로', script: '[2]\n가\n[1]\n나', status: 'fail', numbers: [2, 1] },
  { name: '구분자 없음', script: '가\n나', status: 'fail', numbers: [] },
  { name: '예전 표기뿐', script: 'Slide 1\n가\nSlide 2\n나', status: 'fail', numbers: [] },
  { name: 'CRLF', script: '[1]\r\n가\r\n[2]\r\n나', status: 'success', numbers: [1, 2] },
  { name: '제목 한 줄', script: '제목\n[1]\n가', status: 'success', numbers: [1] },
];

describe('슬라이드 구분자 — FE · AI 공통 입력 목록', () => {
  it.each(LINE_CASES)('%j → %s', (line, expected) => {
    expect(markerNumber(line)).toBe(expected);
  });

  it.each(SCRIPT_CASES)('대본 "$name" → $status', ({ script, status, numbers }) => {
    const scan = scanSlideMarkers(script);
    expect(scan.numbers).toEqual(numbers);
    const ok = scan.numbers.length > 0 && scan.numbersValid && !scan.leadingText;
    expect(ok ? 'success' : 'fail').toBe(status);
  });
});

describe('슬라이드 구분자 — 그 밖의 규칙', () => {
  it.each(['[1]', '[슬라이드 1]', '슬라이드 1:'])('%s 형식을 줄 앞에서 인식한다', (marker) => {
    expect(markerNumber(`  ${marker} 본문`)).toBe(1);
    expect(markerNumber(`본문 중간 ${marker}`)).toBeNull();
    expect(scanSlideMarkers(`${marker}\n첫 장\n${marker.replace('1', '2')}\n둘째 장`)).toEqual({
      numbers: [1, 2],
      numbersValid: true,
      leadingText: false,
      legacy: [],
    });
  });

  it('빈 줄과 전환 표현은 구분자가 아니다', () => {
    expect(scanSlideMarkers('안녕하세요\n\n오늘은\n\n반갑습니다').numbers).toEqual([]);
  });

  it('첫 구분자 앞의 제목 한 줄은 괜찮다 — AI 가 지우고 나눈다', () => {
    expect(scanSlideMarkers('피치코치 소개\n\n[슬라이드 1]\n가')).toMatchObject({
      numbers: [1],
      leadingText: false,
    });
  });

  it('첫 구분자 앞에 두 줄 이상 글이 있으면 알려 준다 — AI 는 그때 나누지 않는다', () => {
    expect(scanSlideMarkers('제목\n인사말\n[슬라이드 1]\n가')).toMatchObject({
      numbers: [1],
      leadingText: true,
    });
    expect(scanSlideMarkers('\n[슬라이드 1]\n가\n[슬라이드 2]\n나')).toMatchObject({
      numbers: [1, 2],
      leadingText: false,
    });
  });

  it('번호가 0 · 겹침 · 거꾸로면 알려 준다 — AI 는 그때 나누지 않는다', () => {
    expect(scanSlideMarkers('[1]\n가\n[2]\n나').numbersValid).toBe(true);
    expect(scanSlideMarkers('[0]\n가').numbersValid).toBe(false);
    expect(scanSlideMarkers('[1]\n가\n[1]\n나').numbersValid).toBe(false);
    expect(scanSlideMarkers('[2]\n가\n[1]\n나').numbersValid).toBe(false);
  });
});

describe('예전 표기 안내', () => {
  it.each(['Slide 1', 'slide 2: 도입', '슬라이드 3', '슬라이드1:', '슬라이드 4.', '1.', '2) 도입'])(
    '%s 는 구분은 아니지만 예전 표기로 알려 준다',
    (line) => {
      expect(markerNumber(line)).toBeNull();
      expect(isLegacyMarker(line)).toBe(true);
    },
  );

  it.each([
    '[1]',
    '슬라이드 1:',
    '3.5%p 늘었습니다',
    '2024. 올해는',
    '오늘은 슬라이드 3 을 봅니다',
    '첫째로',
  ])('%s 는 예전 표기로 알리지 않는다', (line) => {
    expect(isLegacyMarker(line)).toBe(false);
  });

  it('찾은 줄을 앞뒤 공백 없이 모아 준다', () => {
    expect(scanSlideMarkers('  Slide 1  \n가\n[2]\n나\n3.\n다').legacy).toEqual(['Slide 1', '3.']);
  });
});
