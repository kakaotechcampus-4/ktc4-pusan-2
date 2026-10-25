// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { useCreateStore } from './createStore';
import { ScriptPane } from './steps/ScriptPane';
import { CriteriaPane } from './steps/CriteriaPane';
import { toPracticeCombo } from './lib/draft';

vi.mock('@/shared/ui/PdfPage', () => ({ PdfPage: () => null }));
vi.mock('@/shared/lib/usePdfDocument', () => ({ usePdfDocument: () => null }));
vi.mock('./scriptParse', () => ({ startScriptMapping: vi.fn(), retryScriptMapping: vi.fn() }));

let root: Root;
let container: HTMLDivElement;
let query: QueryClient;
const state = () => useCreateStore.getState();
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  state().reset();
  state().setPitchId('pitch1');
  state().attachSlides({
    pageCount: 1,
    fileUrl: '/slides.pdf',
    fileName: 'slides.pdf',
    presentationVersionId: 'pv1',
  });
  state().addScriptVersion('first script');
  state().patchScript(1, {
    remote: { id: 'sv1', version: 1 },
    blocks: ['first script'],
    segmented: true,
  });
  state().saveMapping(1);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(async () => {
  await act(async () => root.unmount());
  query.clear();
  container.remove();
  vi.unstubAllGlobals();
});

it('choosing a saved V1 and pressing next should use V1 for practice', async () => {
  state().addScriptVersion('second script');
  state().patchScript(2, {
    remote: { id: 'sv2', version: 2 },
    blocks: ['second script'],
    segmented: true,
  });
  state().saveMapping(2);
  state().select('script', 1);
  await act(async () => root.render(<ScriptPane script={state().draft.scripts[0]!} />));
  const next = [...container.querySelectorAll('button')].find((b) => b.textContent === '다음 →')!;
  expect(next.disabled).toBe(false);
  await act(async () => next.click());
  expect(toPracticeCombo(state().draft, state().chosen)?.scriptVersionId).toBe('sv1');
});

it('must disable practice when mapping edits have not been reparsed and saved', async () => {
  const start = vi.fn();
  state().editMappingBlock(1, 0, 'changed script');
  state().select('criteria');
  expect(toPracticeCombo(state().draft, state().chosen)).toBeNull();
  await act(async () =>
    root.render(
      <QueryClientProvider client={query}>
        <CriteriaPane criteria={null} onStart={start} />
      </QueryClientProvider>,
    ),
  );
  const skip = [...container.querySelectorAll('button')].find((b) => b.textContent === '건너뛰기')!;
  expect(skip.disabled).toBe(true);
  expect(container.textContent).toContain('대본 매핑을 확인하고 저장해 주세요.');
  await act(async () => skip.click());
  expect(start).not.toHaveBeenCalled();

  await act(async () =>
    state().patchScript(1, {
      remote: { id: 'sv2', version: 2 },
      blocks: ['changed script'],
      segmented: true,
    }),
  );
  expect(skip.disabled).toBe(true);
  await act(async () => state().saveMapping(1));
  expect(skip.disabled).toBe(false);
  await act(async () => skip.click());
  expect(start).toHaveBeenCalledOnce();
});
