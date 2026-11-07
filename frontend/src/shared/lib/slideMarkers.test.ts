import { describe, expect, it } from 'vitest';
import {
  isLegacyMarker,
  isNamedLegacyMarker,
  isWrappedMarker,
  markerNumber,
  scanSlideMarkers,
  stripMarker,
  toAsciiDigits,
} from './slideMarkers';

/**
 * ★ FE · AI 공통 입력 목록 — AI `find_slides` 도 같은 입력에 같은 번호를 내야 합니다.
 * 값은 "구분자로 인식하는 번호", 구분자가 아니면 null.
 *
 * 지금은 **FE 에만 있습니다.** AI `ai/service/tests/unit/script_parser/test_splitter.py` 에는 이 목록이
 * 없어서, AI 정규식(`splitter.py` `DELIMITER_RE`)이 바뀌어도 어느 시험도 잡지 못합니다. 같은 목록을
 * 넣어 달라는(가능하면 두 쪽이 같은 JSON 을 읽게) 요청은 #1023 입니다. 그 전까지는 AI 정규식이
 * 바뀌면 이 목록을 AI 에 직접 넣어 대조합니다 (#826 리뷰에서 57줄 · 17개가 같은 것을 확인).
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
  // 전각 숫자 — Python `\d` 는 전각도 숫자라 AI 는 나눈다. FE 는 검사할 때만 ASCII 로 바꿔 같게 본다
  ['[１]', 1],
  ['[슬라이드 ２]', 2],
  ['슬라이드 ３:', 3],
  ['슬라이드 ３: 도입', 3],
  ['  [４] 들여쓰기', 4],
  ['[０]', 0],
  ['슬라이드 ０:', 0],
  ['[１２]', 12],
  ['[1２]', 12], // 전각과 반각이 섞인 번호
  ['[슬라이드 ３４]', 34],
  ['[９９９９]', 9999],
  ['[１２３４５]', null], // 5자리 이상은 전각이어도 본문
  ['[１００００]', null],
  ['슬라이드 ３', null], // 콜론 없음
  ['슬라이드 ３.', null],
  ['[１', null],
  ['［１］', null], // 괄호가 전각이면 구분자가 아니다
  ['[슬라이드　３]', null], // 공백이 전각이면 구분자가 아니다
  ['[①]', null], // 숫자(Nd)가 아닌 원 숫자
  ['３. 도입', null],
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
  { name: '전각 정상', script: '[１]\n가\n[２]\n나', status: 'success', numbers: [1, 2] },
  {
    name: '전각 형식 섞기',
    script: '[１] 가\n슬라이드 ２: 나\n[슬라이드 ３]\n다',
    status: 'success',
    numbers: [1, 2, 3],
  },
  { name: '전각 반각 섞기', script: '[1]\n가\n[２]\n나', status: 'success', numbers: [1, 2] },
  { name: '전각 번호 0', script: '[０]\n가\n[１]\n나', status: 'fail', numbers: [0, 1] },
  { name: '전각 번호 겹침', script: '[1]\n가\n[１]\n나', status: 'fail', numbers: [1, 1] },
  { name: '전각 번호 거꾸로', script: '[２]\n가\n[１]\n나', status: 'fail', numbers: [2, 1] },
  { name: '전각 두 자리', script: '[９]\n가\n[１０]\n나', status: 'success', numbers: [9, 10] },
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
      wrapped: [],
    });
  });

  it('빈 줄과 전환 표현은 구분자가 아니다', () => {
    expect(scanSlideMarkers('안녕하세요\n\n오늘은\n\n반갑습니다').numbers).toEqual([]);
  });

  it('첫 구분자 앞의 한 줄은 제목으로 어림한다 — AI 는 LLM 이 제목으로 고르면 지우고 나눈다', () => {
    expect(scanSlideMarkers('피치코치 소개\n\n[슬라이드 1]\n가')).toMatchObject({
      numbers: [1],
      leadingText: false,
    });
  });

  it('첫 구분자 앞에 두 줄 이상 글이 있으면 어림으로 알린다 — AI 는 제목이 아닌 줄이 남으면 나누지 않는다(제목 판정은 LLM)', () => {
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

  it('장을 가리킨 표기와 번호 목록 꼴을 나눈다', () => {
    for (const line of ['Slide 1', 'slide 2: 도입', '슬라이드 3', '슬라이드１']) {
      expect(isNamedLegacyMarker(line)).toBe(true);
    }
    for (const line of ['1.', '2) 도입', '[1]', '슬라이드 1:']) {
      expect(isNamedLegacyMarker(line)).toBe(false);
    }
  });

  it('첫 구분자 앞의 줄만 앞뒤 공백 없이 모아 준다', () => {
    expect(scanSlideMarkers('  Slide 1  \n가\n2)\n[3]\n나\n4.\n다').legacy).toEqual([
      'Slide 1',
      '2)',
    ]);
  });

  it('구분자가 없으면 모든 줄에서 찾는다', () => {
    expect(scanSlideMarkers('Slide 1\n가\nSlide 2\n나').legacy).toEqual(['Slide 1', 'Slide 2']);
  });

  it('구분자 뒤 본문의 번호 목록 · 문장은 예전 표기로 세지 않는다', () => {
    const script = '[슬라이드 1]\n가\n[슬라이드 3]\n1. 첫째 이유는\n2) 둘째로\nSlide 3 shows';
    expect(scanSlideMarkers(script)).toEqual({
      numbers: [1, 3],
      numbersValid: true,
      leadingText: false,
      legacy: [],
      wrapped: [],
    });
  });
});

describe('마크다운으로 꾸민 구분자', () => {
  it.each([
    '## [슬라이드 1]',
    '**[슬라이드 1]**',
    '> [1] 도입',
    '- 슬라이드 2: 문제',
    '  # ** [３]',
  ])('%s 는 구분으로 세지 않고 꾸민 구분자로 알려 준다', (line) => {
    expect(markerNumber(line)).toBeNull();
    expect(isWrappedMarker(line)).toBe(true);
  });

  it.each(['[슬라이드 1]', '## 문제 상황', '- 첫째로', '## Slide 1', '본문 ## [1]'])(
    '%s 는 꾸민 구분자가 아니다',
    (line) => {
      expect(isWrappedMarker(line)).toBe(false);
    },
  );

  it('첫 구분자 앞의 줄만 모은다', () => {
    expect(scanSlideMarkers('## [슬라이드 1]\n가\n## [슬라이드 2]\n나')).toMatchObject({
      numbers: [],
      wrapped: ['## [슬라이드 1]', '## [슬라이드 2]'],
    });
    expect(scanSlideMarkers('[슬라이드 1]\n가\n## [슬라이드 2]\n나').wrapped).toEqual([]);
  });
});

describe('전각 숫자', () => {
  it('검사할 때만 ASCII 로 바꾸고 원문은 그대로 둔다', () => {
    const script = '[１]\n가 ２０２４\n[２] 나';
    scanSlideMarkers(script);
    expect(script).toBe('[１]\n가 ２０２４\n[２] 나');
    expect(toAsciiDigits('[１2３] ０９')).toBe('[123] 09');
    expect(toAsciiDigits('[１]').length).toBe('[１]'.length);
  });

  it('구분자 뒤 글은 원문 그대로 남긴다', () => {
    expect(stripMarker('[１] 안녕 ２０２４')).toBe(' 안녕 ２０２４');
    expect(stripMarker('슬라이드 ３４: 도입')).toBe(' 도입');
    expect(stripMarker('본문 [１]')).toBe('본문 [１]');
  });

  it('전각 숫자 예전 표기도 안내한다', () => {
    expect(isLegacyMarker('Slide １')).toBe(true);
    expect(isLegacyMarker('１.')).toBe(true);
    expect(isLegacyMarker('[１]')).toBe(false);
    expect(isLegacyMarker('３.５%p 늘었습니다')).toBe(false);
  });
});
