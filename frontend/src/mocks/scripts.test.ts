import { describe, expect, it } from 'vitest';
import { splitScript } from './scripts';

describe('대본 목 — AI 처럼 명시적 구분자로만 나눈다', () => {
  it('"슬라이드 N" 으로 나눈다', () => {
    const { segmented, slides } = splitScript('슬라이드 1\n안녕하세요.\n\n슬라이드 2\n문제는…');
    expect(segmented).toBe(true);
    expect(slides.map((s) => s.content)).toEqual(['안녕하세요.', '문제는…']);
  });

  it('"Slide N" 과 "N." 도 구분자다', () => {
    expect(splitScript('Slide 1 도입\n가\nSlide 2\n나').slides).toHaveLength(2);
    expect(splitScript('1. 가\n2. 나\n3. 다').slides.map((s) => s.slide_number)).toEqual([1, 2, 3]);
  });

  it('구분자가 없으면 나누지 않고 한 슬라이드로 둔다 — 정상 결과다', () => {
    const { segmented, slides } = splitScript('첫째로 말씀드리면…\n\n다음으로…');
    expect(segmented).toBe(false);
    expect(slides).toHaveLength(1);
  });

  it('앞부분이 구분되지 않았으면 일부만 나누지 않는다', () => {
    expect(splitScript('인사말\n슬라이드 1\n가').segmented).toBe(false);
  });
});
