import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it } from 'vitest';
import { useCreateStore } from '../createStore';
import type { CriteriaVersion } from '../lib/draft';
import { CriteriaPane } from './CriteriaPane';

function renderCriteria(criteria: CriteriaVersion | null = null): string {
  useCreateStore.getState().setPitchId('test-pitch');
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <CriteriaPane criteria={criteria} />
    </QueryClientProvider>,
  );
}

afterEach(() => useCreateStore.getState().reset());

it('지원되는 측정 영역 8개와 대본 사용 설정을 읽기 전용 안내 칩으로 표시한다', () => {
  const html = renderCriteria();
  const measurable = [
    '말 속도',
    '말 사이 멈춤',
    '긴 침묵',
    '소리 크기',
    '군더더기 말',
    '시선',
    '대본 의존도',
    '대본 유사도',
  ];

  for (const item of measurable) {
    expect(html).toContain(`>${item}</li>`);
  }
  expect(html.match(/<li class="rounded-full/g)).toHaveLength(8);
  expect(html).toContain('aria-label="측정 가능한 항목"');
  expect(html).toContain('추가 설정');
  expect(html).toContain('대본 사용 허용');
  expect(html).toContain('발표 시간은 발표정보에서 설정해 주세요.');
  expect(html).toContain('예: 군더더기 말은 5회 이하로 줄이고 싶어요.');
  expect(html).not.toContain('시장 규모와 출처');
});

it('정리된 기준과 제외된 입력 및 수정 방법을 함께 안내한다', () => {
  const criteria: CriteriaVersion = {
    version: 1,
    items: [{ id: '1', text: '군더더기 말은 5회 이하' }],
    saved: true,
    sourceText: '군더더기 말은 5회 이하. 발표 내용은 창의적으로.',
    exceptText: '발표 내용은 창의적으로',
  };
  const html = renderCriteria(criteria);

  expect(html).toContain('이번 발표에 반영된 기준');
  expect(html).toContain('군더더기 말은 5회 이하');
  expect(html).toContain('숫자 목표가 없는 항목은 달성 여부를 판정하지 않아요.');
  expect(html).toContain('평가기준에 넣지 않은 내용');
  expect(html).toContain('발표 내용은 창의적으로');
  expect(html).toContain('측정 가능한 행동으로 바꿔 적어 주세요.');
});
