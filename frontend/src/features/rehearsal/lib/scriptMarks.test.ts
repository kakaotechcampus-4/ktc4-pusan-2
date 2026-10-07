import { describe, expect, it } from 'vitest';
import { markScriptLines, normalizeRanges, rangesFromKeywords } from './scriptMarks';

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
