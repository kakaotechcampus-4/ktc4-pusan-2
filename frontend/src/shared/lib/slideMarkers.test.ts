import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  markerNumber,
  matchMarker,
  nearMarkerKind,
  scanSlideMarkers,
  stripMarker,
  toAsciiDigits,
} from './slideMarkers';

/**
 * ★ FE · AI 공통 입력 목록 — AI `test_splitter.py` 도 같은 파일을 읽어 `find_slides` 와 대조합니다.
 * `frontend/` 밖 파일이라 import 하지 않고 시험 안에서 읽습니다 (앱 번들 · tsconfig 와 상관없습니다).
 * 규칙을 바꾸면 이 파일과 `docs/ai/features/script-parser.md` 5-2 를 함께 고칩니다.
 */
interface Markers {
  /** `rest` 는 구분자 뒤 같은 줄의 글(AI `match_delimiter` 의 두 번째 값). 구분자 줄에만 있습니다 */
  lines: { line: string; number: number | null; rest?: string; group: string; note?: string }[];
  scripts: { name: string; script: string; status: 'success' | 'fail'; numbers: number[] }[];
}
const MARKERS: Markers = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL('../../../../docs/ai/features/script-parser-markers.json', import.meta.url),
    ),
    'utf8',
  ),
);

describe('슬라이드 구분자 — FE · AI 공통 입력 목록 (docs/ai/features/script-parser-markers.json)', () => {
  it('목록을 읽었다', () => {
    expect(MARKERS.lines.length).toBeGreaterThan(0);
    expect(MARKERS.scripts.length).toBeGreaterThan(0);
  });

  it.each(MARKERS.lines.map((c) => [c.line, c.number, c.group, c.rest] as const))(
    '%j → %s (%s)',
    (line, expected, _group, rest) => {
      expect(markerNumber(line)).toBe(expected);
      // 번호와 함께 구분자 뒤 글도 AI 와 같아야 합니다 — 목은 이 글을 그 장의 본문으로 씁니다
      if (rest !== undefined) expect(matchMarker(line)?.rest).toBe(rest);
    },
  );

  it('구분자 줄에는 모두 뒤 글(rest)이 적혀 있다', () => {
    expect(MARKERS.lines.filter((c) => c.number !== null && c.rest === undefined)).toEqual([]);
  });

  // status 는 구분자만으로 본 결과입니다 (AI `find_slides`). 머리글 판정은 LLM 이라 목록 밖입니다
  it.each(MARKERS.scripts)('대본 "$name" → $status', ({ script, status, numbers }) => {
    const scan = scanSlideMarkers(script);
    expect(scan.numbers).toEqual(numbers);
    const ok = scan.numbers.length > 0 && scan.numbersValid;
    expect(ok ? 'success' : 'fail').toBe(status);
  });
});

describe('슬라이드 구분자 — Python 과 JS 가 다른 곳', () => {
  // AI 에서 직접 확인한 값입니다. 공통 목록에 넣기에는 한글 입력기로 칠 일이 드뭅니다
  it.each([
    ['ſlide 1', 1], // Python IGNORECASE 는 ſ 를 s 로 봅니다
    ['SLİDE 1', 1],
    ['slıde 1', 1],
    ['Slide 1\u00a0', 1], // Python strip 은 NBSP 를 걷습니다
    ['Slide 1\x1f', 1], // Python strip 은 \x1f 도 걷습니다 (JS trim 은 아님)
    ['Slide 1\ufeff', null], // Python strip 은 BOM 을 걷지 않습니다 (JS trim 은 걷음)
    ['## Slide 1\u00a0제목', null], // 제목 표시 뒤 구분 공백은 공백 · 탭만
    ['## Slide 1\u3000제목', null],
    ['\u00a0[1]', null], // 줄 앞은 공백 · 탭만 걷습니다
    ['## Slide 1 -2', null],
    ['## Slide １-２', null], // 전각 숫자 범위
    ['[１](x)', null],
  ] as const)('%j → %s', (line, expected) => {
    expect(markerNumber(line)).toBe(expected);
  });
});

describe('슬라이드 구분자 — 그 밖의 규칙', () => {
  it.each(['[1]', '[슬라이드 1]', '슬라이드 1:', 'Slide 1', '## Slide 1 — 도입'])(
    '%s 형식을 줄 앞에서 인식한다',
    (marker) => {
      expect(markerNumber(`  ${marker}`)).toBe(1);
      expect(markerNumber(`본문 중간 ${marker}`)).toBeNull();
      expect(scanSlideMarkers(`${marker}\n첫 장\n${marker.replace('1', '2')}\n둘째 장`)).toEqual({
        numbers: [1, 2],
        numbersValid: true,
        leadingText: false,
        near: [],
      });
    },
  );

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

describe('구분처럼 보이지만 인식되지 않는 줄', () => {
  it.each([
    ['\u3000[1]', 'space'],
    ['\u00a0슬라이드 1:', 'space'],
    ['\ufeff[슬라이드 1]', 'space'],
    ['【슬라이드 1】', 'form'],
    ['［１］', 'form'],
    ['[ 1 ]', 'form'],
    ['**[슬라이드 1]**', 'form'],
    ['**슬라이드 1**', 'form'],
    ['> [1] 도입', 'form'],
    ['- 슬라이드 2: 문제', 'form'],
    ['#Slide 1', 'form'],
    ['####### Slide 1', 'form'],
    ['슬라이드\u00a01', 'form'],
    ['슬라이드 1.', 'named'],
    ['슬라이드 1 - 도입', 'named'],
    ['Slide 3 shows the result', 'named'],
    ['1.', 'numbered'],
    ['2) 도입', 'numbered'],
  ] as const)('%j 는 %s', (line, kind) => {
    expect(markerNumber(line)).toBeNull();
    expect(nearMarkerKind(line)).toBe(kind);
  });

  it.each([
    '[1]',
    'Slide 1',
    '## [슬라이드 1]',
    '슬라이드1:',
    '3.5%p 늘었습니다',
    '2024. 올해는',
    '**슬라이드 3 보시면**',
    '- 첫째로',
    '본문 ## [1]',
    '첫째로',
    '',
  ])('%j 는 짚지 않는다', (line) => {
    expect(nearMarkerKind(line)).toBeNull();
  });

  it('첫 구분자 앞의 줄만 앞뒤 공백 없이 모아 준다', () => {
    expect(scanSlideMarkers('  슬라이드 1.  \n가\n2)\n[3]\n나\n4.\n**[5]**\n다').near).toEqual([
      { line: '슬라이드 1.', kind: 'named' },
      { line: '2)', kind: 'numbered' },
    ]);
  });

  it('구분자가 없으면 모든 줄에서 찾는다', () => {
    expect(scanSlideMarkers('**[슬라이드 1]**\n가\n**[슬라이드 2]**\n나').near).toEqual([
      { line: '**[슬라이드 1]**', kind: 'form' },
      { line: '**[슬라이드 2]**', kind: 'form' },
    ]);
  });

  it('구분자 뒤 본문의 번호 목록 · 문장 · 인용은 짚지 않는다', () => {
    const script =
      '[슬라이드 1]\n가\n[슬라이드 3]\n1. 첫째 이유는\n2) 둘째로\nSlide 3 shows\n> [1] 인용';
    expect(scanSlideMarkers(script)).toEqual({
      numbers: [1, 3],
      numbersValid: true,
      leadingText: false,
      near: [],
    });
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
    expect(stripMarker('[１] 안녕 ２０２４')).toBe('안녕 ２０２４');
    expect(stripMarker('슬라이드 ３４: 도입')).toBe('도입');
    expect(stripMarker('## Slide ３ — ２부')).toBe('２부');
    expect(stripMarker('본문 [１]')).toBe('본문 [１]');
  });
});
