// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { saveStandards } from '@/shared/api/standards';
import type { StandardsSaved } from '@/types/standards';
import { useCreateStore } from './createStore';
import type { CriteriaVersion } from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';

vi.mock('@/shared/api/standards', () => ({ saveStandards: vi.fn(), parseStandards: vi.fn() }));

function CurrentCriteria() {
  const criteria = useCreateStore((s) => s.draft.criteria.at(-1) ?? null);
  return <CriteriaPane criteria={criteria} />;
}

it('저장 중 표시 버전이 바뀌어도 요청을 시작한 버전에 응답을 반영한다', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const store = () => useCreateStore.getState();
  store().reset();
  store().setPitchId('p1');
  store().select('criteria');
  store().applyParsedCriteria(null, {
    version: 1,
    sourceText: '원문',
    standards: ['원래 기준'],
    exceptText: null,
  });
  store().editCriterion(1, store().draft.criteria[0]!.items[0]!.id, '저장할 기준');
  const original = store().markCriteriaSaved;
  const saved = vi.fn(original);
  useCreateStore.setState({ markCriteriaSaved: saved });
  let resolve!: (value: StandardsSaved) => void;
  vi.mocked(saveStandards).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const query = new QueryClient();
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <QueryClientProvider client={query}>
          <CurrentCriteria />
        </QueryClientProvider>,
      ),
    );
    await act(async () => {
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === '고친 내용 저장')!
        .click();
    });
    expect(saveStandards).toHaveBeenCalledWith('p1', ['저장할 기준']);
    await act(async () =>
      store().applyParsedCriteria(1, {
        version: 2,
        sourceText: '재정리',
        standards: ['다른 기준'],
        exceptText: null,
      }),
    );
    await act(async () => {
      resolve({
        pitch_id: 'p1',
        version: 3,
        standards: [{ standard: '저장할 기준' }],
        except_standard: null,
      });
      await new Promise((done) => setTimeout(done, 30));
    });
    expect(saved).toHaveBeenCalledWith(1, expect.objectContaining({ version: 3 }));
  } finally {
    await act(async () => root.unmount());
    query.clear();
    container.remove();
    useCreateStore.setState({ markCriteriaSaved: original });
    store().reset();
    vi.unstubAllGlobals();
  }
});

/** 실제 Zustand store로 렌더링해 UI와 버전 상태를 함께 검증합니다. */
async function mountCriteria(criteria: CriteriaVersion | null) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const query = new QueryClient();
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () =>
    root.render(
      <QueryClientProvider client={query}>
        <CriteriaPane criteria={criteria} />
      </QueryClientProvider>,
    ),
  );
  return {
    host,
    rerender: async (next: CriteriaVersion | null) => {
      await act(async () =>
        root.render(
          <QueryClientProvider client={query}>
            <CriteriaPane criteria={next} />
          </QueryClientProvider>,
        ),
      );
    },
    cleanup: async () => {
      await act(async () => root.unmount());
      query.clear();
      host.remove();
      useCreateStore.getState().reset();
      vi.unstubAllGlobals();
    },
  };
}

it('8개 항목은 한 번에 한 예시만 보여 주고, 판정 한계를 정확하게 안내한다', async () => {
  const store = useCreateStore.getState();
  store.reset();
  store.setPitchId('p1');
  const view = await mountCriteria(null);
  try {
    const chips = [
      ...view.host.querySelectorAll<HTMLButtonElement>('button[aria-label$="작성 예시 보기"]'),
    ];
    expect(chips).toHaveLength(8);
    expect(view.host.textContent).toContain('침묵 길이·절대 음량(dB)');
    expect(view.host.textContent).not.toContain('대본 사용 허용');
    expect(view.host.textContent).not.toContain(
      '숫자 목표가 없는 항목은 달성 여부를 판정하지 않아요.',
    );

    await act(async () => {
      chips[0]!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    });
    expect(view.host.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
    expect(view.host.querySelector('[role="tooltip"]')?.textContent).toContain('1분에 300자 이하');

    await act(async () => {
      chips[6]!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    });
    expect(view.host.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
    expect(view.host.querySelector('[role="tooltip"]')?.textContent).toContain(
      '대본을 읽으며 말하는 비율',
    );
    expect(view.host.querySelector('[role="tooltip"]')?.textContent).toContain('대본을 연결한 경우');

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(view.host.querySelector('[role="tooltip"]')).toBeNull();
  } finally {
    await view.cleanup();
  }
});

it('Esc로 예시를 닫아도 칩의 키보드 포커스가 유지된다', async () => {
  useCreateStore.getState().reset();
  useCreateStore.getState().setPitchId('p1');
  const view = await mountCriteria(null);
  try {
    const chip = view.host.querySelector<HTMLButtonElement>(
      'button[aria-label="말 속도 작성 예시 보기"]',
    )!;
    await act(async () => chip.focus());
    expect(document.activeElement).toBe(chip);
    expect(view.host.querySelector('[role="tooltip"]')?.textContent).toContain('1분에 300자 이하');

    await act(async () => {
      chip.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(view.host.querySelector('[role="tooltip"]')).toBeNull();
    expect(document.activeElement).toBe(chip);

    await act(async () => chip.blur());
    expect(view.host.querySelector('[role="tooltip"]')).toBeNull();
  } finally {
    await view.cleanup();
  }
});

it('지난 버전과 저장하지 않은 수정본을 실제 반영된 기준이라고 표시하지 않는다', async () => {
  const store = useCreateStore.getState();
  store.reset();
  store.setPitchId('p1');
  store.applyParsedCriteria(null, {
    version: 1,
    sourceText: '군더더기 말 5회 이하',
    standards: ['군더더기 말 5회 이하'],
    exceptText: '발표 주제는 창의적으로',
  });
  store.applyParsedCriteria(1, {
    version: 2,
    sourceText: '청중 70% 이상 바라보기',
    standards: ['청중 70% 이상 바라보기'],
    exceptText: null,
  });
  const old = useCreateStore.getState().draft.criteria[0]!;
  const view = await mountCriteria(old);
  try {
    expect(view.host.querySelector('h3')?.textContent).not.toBe('이번 발표에 반영된 기준');
    expect(view.host.textContent).toContain('정리된 평가기준');
    expect(view.host.textContent).toContain('군더더기 말 5회 이하');
    expect(view.host.textContent).toContain('측정 지표와 연결되지 않으면');

    const current = useCreateStore.getState().draft.criteria[1]!;
    await act(async () =>
      useCreateStore.getState().editCriterion(2, current.items[0]!.id, '청중을 80% 바라보기'),
    );
    await view.rerender(useCreateStore.getState().draft.criteria[1]!);
    expect(view.host.textContent).toContain('정리된 평가기준');
    expect(view.host.textContent).toContain('고친 내용 저장 안 됨');
    expect(view.host.textContent).not.toContain('이번 발표에 반영된 기준');
  } finally {
    await view.cleanup();
  }
});
