import { describe, expect, it } from 'vitest';
import {
  codePointRangesToUtf16,
  markScriptLines,
  normalizeRanges,
  rangesFromKeywords,
} from './scriptMarks';

const text = (parts: { text: string; mark: boolean }[]) =>
  parts.map((p) => (p.mark ? `[${p.text}]` : p.text)).join('');

describe('대본 강조 조각', () => {
  it('AI 가 준 highlights 위치를 그대로 강조한다', () => {
    const content = '안녕하세요 피치코치입니다';
    const lines = markScriptLines(content, [{ start: 6, end: 10 }], []);
    expect(lines.map(text)).toEqual(['안녕하세요 [피치코치]입니다']);
  });

  it('highlights 가 없으면 키워드가 처음 나오는 자리를 강조한다', () => {
    const content = '시선과 속도, 그리고 다시 시선';
    const lines = markScriptLines(content, [], ['시선', '속도']);
    expect(lines.map(text)).toEqual(['[시선]과 [속도], 그리고 다시 시선']);
  });

  it('대본에 없는 키워드는 건너뛴다', () => {
    expect(rangesFromKeywords('발표 연습', ['없는 말', '연습'])).toEqual([{ start: 3, end: 5 }]);
  });

  it('줄로 나눠도 강조 위치가 어긋나지 않는다 — 위치는 content 전체 기준', () => {
    const content = '첫 줄입니다\n  둘째 줄의 키워드\n';
    const start = content.indexOf('키워드');
    const lines = markScriptLines(content, [{ start, end: start + 3 }], []);
    expect(lines.map(text)).toEqual(['첫 줄입니다', '둘째 줄의 [키워드]']);
  });

  it('여러 줄에 걸친 범위는 줄마다 나뉘어 강조된다', () => {
    const content = '가나다\n라마바';
    const lines = markScriptLines(content, [{ start: 1, end: 6 }], []);
    expect(lines.map(text)).toEqual(['가[나다]', '[라마]바']);
  });

  it('겹치거나 밖으로 나간 범위를 정리한다 — AI 값이 틀려도 대본은 깨지지 않는다', () => {
    expect(
      normalizeRanges(
        [
          { start: 5, end: 99 },
          { start: -3, end: 2 },
          { start: 1, end: 3 },
          { start: 4, end: 4 },
        ],
        8,
      ),
    ).toEqual([
      { start: 0, end: 3 },
      { start: 5, end: 8 },
    ]);
  });

  it('빈 줄은 빠지고 강조가 없으면 한 조각이다', () => {
    expect(markScriptLines('\n\n하나\n\n', [], [])).toEqual([[{ text: '하나', mark: false }]]);
  });
});

/**
 * AI(Python)는 `highlights` 를 유니코드 코드 포인트로 세고, JS 는 UTF-16 으로 셉니다.
 * 이모지 같은 보조 평면 문자가 앞에 있으면 두 값이 달라집니다 — 멘토 리뷰에서 짚은 사례입니다.
 * 아래 위치는 모두 Python 에서 `text.index(...)` 로 얻는 코드 포인트 위치입니다.
 */
describe('AI 강조 위치의 단위 — 코드 포인트를 UTF-16 으로', () => {
  it('이모지가 앞에 있어도 AI 가 가리킨 낱말을 강조한다 ("😀 핵심" 의 2:4)', () => {
    const lines = markScriptLines('😀 핵심', [{ start: 2, end: 4 }], []);
    expect(lines.map(text)).toEqual(['😀 [핵심]']);
  });

  it('이모지가 여러 개면 그만큼 모두 바로잡는다', () => {
    // Python: '🎤🎤 발표 😀 시선'.index('시선') == 8
    const lines = markScriptLines('🎤🎤 발표 😀 시선', [{ start: 8, end: 10 }], []);
    expect(lines.map(text)).toEqual(['🎤🎤 발표 😀 [시선]']);
  });

  it('앞 줄에 이모지가 있어도 다음 줄의 강조가 밀리지 않는다', () => {
    // Python: '첫 줄 😀\n둘째 줄 키워드'.index('키워드') == 11
    const lines = markScriptLines('첫 줄 😀\n둘째 줄 키워드', [{ start: 11, end: 14 }], []);
    expect(lines.map(text)).toEqual(['첫 줄 😀', '둘째 줄 [키워드]']);
  });

  it('결합 이모지(여러 코드 포인트)가 앞에 있어도 맞는다', () => {
    // 👨‍👩‍👧 은 코드 포인트 5개(사람 3 + 잇는 문자 2). Python: '👨‍👩‍👧 가족 소개'.index('소개') == 9
    const lines = markScriptLines('👨‍👩‍👧 가족 소개', [{ start: 9, end: 11 }], []);
    expect(lines.map(text)).toEqual(['👨‍👩‍👧 가족 [소개]']);
  });

  it('보조 평면 한자(𠮷)도 이모지와 같게 바로잡는다', () => {
    // Python: '𠮷野家 메뉴'.index('메뉴') == 4
    const lines = markScriptLines('𠮷野家 메뉴', [{ start: 4, end: 6 }], []);
    expect(lines.map(text)).toEqual(['𠮷野家 [메뉴]']);
  });

  it('기본 평면 글자만 있으면 위치가 그대로다', () => {
    expect(codePointRangesToUtf16('안녕하세요 피치코치', [{ start: 6, end: 10 }])).toEqual([
      { start: 6, end: 10 },
    ]);
  });

  it('대본 밖을 가리키는 위치는 끝으로 붙인다 — 대본이 깨지지 않는다', () => {
    expect(codePointRangesToUtf16('😀가', [{ start: 1, end: 99 }])).toEqual([{ start: 2, end: 3 }]);
  });

  it('키워드로 찾은 위치(JS indexOf)는 바꾸지 않는다', () => {
    const lines = markScriptLines('😀 핵심 정리', [], ['핵심']);
    expect(lines.map(text)).toEqual(['😀 [핵심] 정리']);
  });
});
