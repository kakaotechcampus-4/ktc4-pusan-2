// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { updatePitch } from '@/shared/api/pitch';
import { useCreateStore } from './createStore';
import { PitchCreatePage } from './PitchCreatePage';
import { ScriptPane } from './steps/ScriptPane';
import { CriteriaPane } from './steps/CriteriaPane';
import { toPracticeCombo } from './lib/draft';

vi.mock('@/shared/ui/PdfPage', () => ({ PdfPage: () => null }));
vi.mock('@/shared/lib/usePdfDocument', () => ({ usePdfDocument: () => null }));
vi.mock('@/shared/lib/pdf', () => ({ openPdf: vi.fn() }));
vi.mock('./scriptParse', () => ({ startScriptMapping: vi.fn(), retryScriptMapping: vi.fn() }));
vi.mock('@/shared/api/pitch', () => ({ updatePitch: vi.fn(), createPitch: vi.fn() }));

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
  vi.resetAllMocks();
});

async function renderCreatePage() {
  state().beginEntry('default');
  await act(async () =>
    root.render(
      <QueryClientProvider client={query}>
        <MemoryRouter initialEntries={['/pitch/new']}>
          <Routes>
            <Route path="/pitch/new" element={<PitchCreatePage />} />
            <Route path="/pitch/:id/device-check" element={<p>장치 점검 도착</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

async function clickButton(label: string) {
  const button = [...container.querySelectorAll('button')].find((b) => b.textContent === label);
  expect(button).toBeDefined();
  expect(button!.disabled).toBe(false);
  await act(async () => button!.click());
}

it.each(['건너뛰기', '연습 시작 →'])(
  '%s는 미저장 제목을 발표정보로 돌려보내고 저장 후에만 장치 점검으로 이동한다',
  async (label) => {
    // 새 작성 진입 시 준비된 자료가 초기화되지 않도록 같은 기록 항목을 사용합니다.
    useCreateStore.setState({ entryKey: 'default' });
    state().startFromFile('pitch1', { ...state().draft, title: 'Plato ch01' });
    state().editInfo({ title: '기계공학 발표' });
    if (label === '연습 시작 →') {
      state().applyParsedCriteria(null, {
        sourceText: '출처 말하기',
        standards: ['출처 말하기'],
        exceptText: null,
      });
    }
    state().select('criteria');
    await renderCreatePage();

    await clickButton(label);
    expect(state().node).toBe('info');
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('발표정보를 저장');
    expect(container.textContent).not.toContain('장치 점검 도착');
    expect(container.querySelector('input')?.value).toBe('기계공학 발표');
    expect(updatePitch).not.toHaveBeenCalled();

    vi.mocked(updatePitch).mockResolvedValue({ message: 'ok', pitch_id: 'pitch1' });
    await clickButton('저장하고 다음 →');
    await act(async () => {
      await vi.waitFor(() => expect(state().node).toBe('criteria'));
    });
    expect(updatePitch).toHaveBeenCalledWith(
      'pitch1',
      expect.objectContaining({ title: '기계공학 발표' }),
    );
    expect(state().infoEdits).toBeNull();
    expect(container.querySelector('[role="alert"]')).toBeNull();
    await clickButton(label);
    expect(container.textContent).toContain('장치 점검 도착');
  },
);

it('발표정보를 고치지 않았으면 바로 장치 점검으로 이동한다', async () => {
  useCreateStore.setState({ entryKey: 'default' });
  state().setMeta({ ...state().draft, title: 'Plato ch01' });
  state().select('criteria');
  await renderCreatePage();
  await clickButton('건너뛰기');
  expect(container.textContent).toContain('장치 점검 도착');
  expect(updatePitch).not.toHaveBeenCalled();
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
