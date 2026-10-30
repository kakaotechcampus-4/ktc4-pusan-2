// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { updatePitch } from '@/shared/api/pitch';
import { useCreateStore } from './createStore';
import { PitchCreatePage } from './PitchCreatePage';
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
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    if (this.open) {
      this.open = false;
      this.dispatchEvent(new Event('close'));
    }
  };
  state().reset();
  state().setPitchId('pitch1');
  state().attachSlides({
    pageCount: 1,
    fileUrl: '/slides.pdf',
    fileName: 'slides.pdf',
    presentationVersionId: 'pv1',
  });
  state().confirmSlides(1);
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

it.each([false, true])(
  '평가기준 유무(%s)와 관계없이 미저장 제목을 발표정보로 돌려보내고 저장 후에만 장치 점검으로 이동한다',
  async (hasCriteria) => {
    // 새 작성 진입 시 준비된 자료가 초기화되지 않도록 같은 기록 항목을 사용합니다.
    useCreateStore.setState({ entryKey: 'default' });
    state().startFromFile('pitch1', { ...state().draft, title: 'Plato ch01' });
    state().editInfo({ title: '기계공학 발표' });
    if (hasCriteria) {
      state().applyParsedCriteria(null, {
        version: 1,
        sourceText: '출처 말하기',
        standards: ['출처 말하기'],
        exceptText: null,
      });
      state().confirmCriteria(1);
    }
    state().select('criteria');
    await renderCreatePage();

    await clickButton('발표정보 확인하기 ↓');
    expect(state().node).toBe('info');
    expect(container.textContent).not.toContain('장치 점검 도착');
    expect(container.querySelector<HTMLInputElement>('#create-info input')?.value).toBe(
      '기계공학 발표',
    );
    expect(updatePitch).not.toHaveBeenCalled();

    vi.mocked(updatePitch).mockResolvedValue({ message: 'ok', pitch_id: 'pitch1' });
    await clickButton('발표정보 저장');
    await act(async () => {
      await vi.waitFor(() => expect(container.textContent).toContain('최종 확인 →'));
    });
    await clickButton('최종 확인 →');
    expect(container.textContent).not.toContain('장치 점검 도착');
    await clickButton('확인하고 시작 전 점검 →');
    await act(async () => {
      await vi.waitFor(() => expect(container.textContent).toContain('장치 점검 도착'));
    });
    expect(updatePitch).toHaveBeenCalledWith(
      'pitch1',
      expect.objectContaining({ title: '기계공학 발표' }),
    );
    expect(state().infoEdits).toBeNull();
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.textContent).toContain('장치 점검 도착');
  },
);

it('발표정보를 고치지 않았어도 최종 확인 뒤 장치 점검으로 이동한다', async () => {
  useCreateStore.setState({ entryKey: 'default' });
  state().setMeta({ ...state().draft, title: 'Plato ch01' });
  state().select('criteria');
  await renderCreatePage();
  await clickButton('최종 확인 →');
  expect(container.textContent).not.toContain('장치 점검 도착');
  await clickButton('확인하고 시작 전 점검 →');
  expect(container.textContent).toContain('장치 점검 도착');
  expect(updatePitch).not.toHaveBeenCalled();
});

it('저장된 대본 V1을 본문에서 선택한 조합으로 점검에 넘긴다', async () => {
  useCreateStore.setState({ entryKey: 'default' });
  state().setMeta({ ...state().draft, title: '발표' });
  state().addScriptVersion('second script');
  state().patchScript(2, {
    remote: { id: 'sv2', version: 2 },
    blocks: ['second script'],
    segmented: true,
  });
  state().saveMapping(2);
  state().select('script', 1);
  await renderCreatePage();
  await clickButton('최종 확인 →');
  expect(container.textContent).not.toContain('장치 점검 도착');
  await clickButton('확인하고 시작 전 점검 →');
  expect(toPracticeCombo(state().draft, state().chosen)?.scriptVersionId).toBe('sv1');
});

it('고친 매핑을 다시 매핑하고 저장하기 전에는 점검으로 이동하지 않는다', async () => {
  useCreateStore.setState({ entryKey: 'default' });
  state().setMeta({ ...state().draft, title: '발표' });
  state().editMappingBlock(1, 0, 'changed script');
  state().select('criteria');
  await renderCreatePage();
  await clickButton('대본 연결 확인하기 ↓');
  expect(container.textContent).not.toContain('장치 점검 도착');
  await act(async () =>
    state().patchScript(1, {
      remote: { id: 'sv2', version: 2 },
      blocks: ['changed script'],
      segmented: true,
    }),
  );
  expect(toPracticeCombo(state().draft, state().chosen)).toBeNull();
  await act(async () => state().saveMapping(1));
  await clickButton('최종 확인 →');
  expect(container.textContent).not.toContain('장치 점검 도착');
  await clickButton('확인하고 시작 전 점검 →');
  expect(container.textContent).toContain('장치 점검 도착');
});
