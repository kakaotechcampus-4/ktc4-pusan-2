// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import type { CriteriaVersion } from '../lib/draft';
import { CriteriaPane } from './CriteriaPane';

/** 서버 렌더링에서는 Zustand의 초기 스냅샷이 사용되므로 화면 테스트에서는 작성 상태를 고정합니다. */
vi.mock('../createStore', () => {
  const mockStore = {
    draftId: 'test-draft',
    pitchId: 'test-pitch',
    criteriaSourceEdits: {},
    draft: { criteria: [] },
    pendingCriteriaReviews: [],
    editCriteriaSource: () => {},
    applyParsedCriteria: () => {},
  };
  return {
    useCreateStore: (selector: (snapshot: typeof mockStore) => unknown) => selector(mockStore),
    isCurrentDraft: () => true,
  };
});

function renderCriteria(criteria: CriteriaVersion | null = null): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <CriteriaPane criteria={criteria} />
    </QueryClientProvider>,
  );
}

it('지원되는 측정 영역 8개만 읽기 전용 안내 칩으로 표시한다', () => {
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
    expect(html).toContain(`aria-label="${item} 작성 예시 보기"`);
  }
  expect(html.match(/role="tooltip"/g)).toHaveLength(8);
  expect(html).toContain('1분에 300자 이하로 말하기');
  expect(html).toContain('소리 크기는 평소 목소리 대비 변화로 측정해요.');
  expect(html).toContain('sm:absolute');
  expect(html).toContain('aria-label="측정 가능한 항목"');
  expect(html).not.toContain('추가 설정');
  expect(html).not.toContain('대본 사용 허용');
  expect(html).toContain('칩에 마우스를 올리거나 눌러 예시를 확인해 보세요.');
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

afterEach(() => vi.unstubAllGlobals());

it('칩의 호버·키보드 초점·터치 클릭으로 예시가 열리고 Esc와 초점 해제 시 닫힌다', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);

  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <CriteriaPane criteria={null} />
        </QueryClientProvider>,
      ),
    );

    const chip = host.querySelector<HTMLButtonElement>(
      'button[aria-label="말 속도 작성 예시 보기"]',
    );
    expect(chip).not.toBeNull();
    const tooltipId = chip!.getAttribute('aria-describedby');
    const tooltip = document.getElementById(tooltipId!);
    expect(tooltip?.getAttribute('role')).toBe('tooltip');
    expect(tooltip?.textContent).toContain('1분에 300자 이하로 말하기');
    expect(tooltip?.classList.contains('invisible')).toBe(true);

    await act(async () => {
      chip!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    });
    expect(tooltip?.classList.contains('visible')).toBe(true);

    await act(async () => {
      chip!.dispatchEvent(
        new MouseEvent('mouseout', { bubbles: true, relatedTarget: document.body }),
      );
    });
    expect(tooltip?.classList.contains('invisible')).toBe(true);

    await act(async () => chip!.focus());
    expect(tooltip?.classList.contains('visible')).toBe(true);

    await act(async () => {
      chip!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(tooltip?.classList.contains('invisible')).toBe(true);
    expect(document.activeElement).not.toBe(chip);

    await act(async () => chip!.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(tooltip?.classList.contains('visible')).toBe(true);

    await act(async () => chip!.focus());
    await act(async () => chip!.blur());
    expect(tooltip?.classList.contains('invisible')).toBe(true);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
