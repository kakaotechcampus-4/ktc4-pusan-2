// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { saveStandards } from '../api/standards';
import type { StandardsSaved } from '@/types/standards';
import { useCreateStore } from './createStore';
import { CriteriaPane } from './steps/CriteriaPane';

vi.mock('../api/standards', () => ({ saveStandards: vi.fn(), parseStandards: vi.fn() }));

function CurrentCriteria() {
  const criteria = useCreateStore((s) => s.draft.criteria.at(-1) ?? null);
  return <CriteriaPane criteria={criteria} onStart={vi.fn()} />;
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
