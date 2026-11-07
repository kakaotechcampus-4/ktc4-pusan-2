import { describe, expect, it } from 'vitest';
import { splitScript } from './scripts';

describe('대본 목 — AI 처럼 명시적 구분자로만 나눈다', () => {
  it.each(['[1]', '[슬라이드 1]', '슬라이드 1:', '[Slide 1]', 'Slide 1:', '## Slide 1'])(
    '%s 형식으로 나누고 구분자를 제거한다',
    (marker) => {
      const { segmented, slides } = splitScript(
        `${marker} 첫 장\n${marker.replace('1', '2')}\n둘째 장`,
      );
      expect(segmented).toBe(true);
      expect(slides.map((s) => s.slide_number)).toEqual([1, 2]);
      expect(slides.map((s) => s.content)).toEqual(['첫 장', '둘째 장']);
    },
  );

  it('대괄호 뒤 본문 문장부호는 남긴다', () => {
    expect(splitScript('[1] - 첫 항목').slides[0]?.content).toBe('- 첫 항목');
  });

  it.each([
    ['슬라이드 1\n안녕하세요.\n\n슬라이드 2\n문제는…', ['안녕하세요.', '문제는…']],
    ['Slide 1\n가\nSlide 2\n나', ['가', '나']],
    ['# 슬라이드 1 도입\n가\n## Slide 2 - 문제\n나', ['도입\n가', '문제\n나']],
  ])('줄 전체 · 제목 표시 뒤 형식도 나눈다 (script_parser-1.3): %j', (text, contents) => {
    const { segmented, slides } = splitScript(text);
    expect(segmented).toBe(true);
    expect(slides.map((s) => s.content)).toEqual(contents);
  });

  it.each([
    'Slide 1 도입\n가\nSlide 2 도입\n나',
    '1. 가\n2. 나\n3. 다',
    '**[슬라이드 1]**\n가\n**[슬라이드 2]**\n나',
    '[1](https://a.b) 링크\n가',
  ])('구분자가 아닌 표기로는 나누지 않는다 — AI 와 같다: %j', (text) => {
    const { segmented, slides } = splitScript(text);
    expect(segmented).toBe(false);
    expect(slides).toHaveLength(1);
  });

  it.each(['[0]\n가\n[1]\n나', '[1]\n가\n[1]\n나', '[2]\n가\n[1]\n나'])(
    '번호가 0 · 겹침 · 거꾸로면 나누지 않고 구분자를 뺀 전체를 한 슬라이드로 둔다: %j',
    (text) => {
      const { segmented, slides } = splitScript(text);
      expect(segmented).toBe(false);
      expect(slides).toHaveLength(1);
      expect(slides[0]?.content).toBe('가\n나');
    },
  );

  it('원문에 적힌 번호를 그대로 쓴다 — 건너뛴 번호를 채우지 않는다', () => {
    const { slides } = splitScript('[슬라이드 1]\n가\n[슬라이드 4]\n라');
    expect(slides.map((s) => s.slide_number)).toEqual([1, 4]);
  });

  it('구분자가 없으면 나누지 않고 한 슬라이드로 둔다 — 정상 결과다', () => {
    const { segmented, slides } = splitScript('첫째로 말씀드리면…\n\n다음으로…');
    expect(segmented).toBe(false);
    expect(slides).toHaveLength(1);
  });

  it('맨 위 한 줄은 제목으로 보고 지우고 나눈다 — FE 어림 (AI 는 LLM 이 제목을 고른다)', () => {
    const result = splitScript('피치코치 소개\n[슬라이드 1]\n가\n[슬라이드 2]\n나');
    expect(result.segmented).toBe(true);
    expect(result.slides.map((s) => s.content)).toEqual(['가', '나']);
  });

  it('앞부분이 두 줄 이상 구분되지 않았으면 나누지 않는다 — FE 어림', () => {
    expect(splitScript('제목\n인사말\n[슬라이드 1]\n가').segmented).toBe(false);
  });
});
