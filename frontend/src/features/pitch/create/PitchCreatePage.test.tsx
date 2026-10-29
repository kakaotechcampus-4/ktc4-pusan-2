// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createPitch, updatePitch } from '@/shared/api/pitch';
import { PitchCreatePage } from './PitchCreatePage';
import { useCreateStore } from './createStore';

vi.mock('@/shared/api/useLogout', () => ({ useLogout: () => ({ mutate: vi.fn() }) }));
vi.mock('@/shared/lib/usePdfDocument', () => ({ usePdfDocument: () => null }));
vi.mock('@/shared/ui/PdfPage', () => ({ PdfPage: () => null }));
vi.mock('@/shared/api/pitch', () => ({ createPitch: vi.fn(), updatePitch: vi.fn() }));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const state = () => useCreateStore.getState();
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((el) => el.textContent?.trim() === text)!;
function Destination() {
  const location = useLocation();
  return <output data-testid="practice">{JSON.stringify(location.state)}</output>;
}
async function mount() {
  await act(async () =>
    root.render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
          })
        }
      >
        <MemoryRouter initialEntries={[{ pathname: '/pitch/new', key: 'page-test' }]}>
          <Routes>
            <Route path="/pitch/new" element={<PitchCreatePage />} />
            <Route path="/pitch/:pitchId/device-check" element={<Destination />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}
function ready() {
  state().setPitchId('pitch-test');
  state().setMeta({
    ...state().draft,
    title: '원래 제목',
    presentationDate: '2026-11-01',
    lowerToleranceSec: 30,
    upperToleranceSec: 60,
  });
  state().attachSlides({
    pageCount: 1,
    presentationVersionId: 'pv1',
    fileName: '발표.pdf',
    fileUrl: '/test.pdf',
  });
  state().addScriptVersion('[1]\n발표 내용');
  state().patchScript(1, {
    remote: { id: 'sv1', version: 1 },
    blocks: ['발표 내용'],
    segmented: true,
  });
  state().saveMapping(1);
  state().select('slides', 1);
}

it('이번 매핑이 성공하면 확인 창을 열고 실패나 기존 자료 열람에는 열지 않는다', async () => {
  ready();
  await mount();
  expect(host.querySelector('dialog')).toBeNull();
  await act(async () => {
    state().unmapScript(1);
    state().patchScript(1, { parse: { status: 'pending' } });
  });
  expect(host.querySelector('dialog')).toBeNull();
  await act(async () => state().patchScript(1, { parse: { status: 'failed', message: '실패' } }));
  expect(host.querySelector('dialog')).toBeNull();
  await act(async () => state().patchScript(1, { parse: { status: 'pending' } }));
  await act(async () =>
    state().patchScript(1, { parse: { status: 'idle' }, blocks: ['새 매핑 결과'] }),
  );
  expect(host.querySelector('dialog')?.textContent).toContain('매핑 확인·수정');
  expect(host.querySelector('dialog textarea')?.textContent).toBe('새 매핑 결과');
  await act(async () => button('닫기').click());
  await act(async () => state().select('info'));
  expect(host.querySelector('dialog')).toBeNull();
});

it('연결할 슬라이드 메뉴에서 키보드 선택과 닫기를 지원한다', async () => {
  ready();
  const draft = state().draft;
  useCreateStore.setState({
    draft: { ...draft, slides: [...draft.slides, { ...draft.slides[0]!, version: 2 }] },
  });
  state().unmapScript(1);
  await mount();
  const trigger = host.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')!;
  await act(async () => trigger.click());
  const options = host.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
  expect(document.activeElement).toBe(options[0]);
  await act(async () =>
    options[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })),
  );
  expect(document.activeElement).toBe(options[1]);
  await act(async () => options[1]!.click());
  expect(state().draft.scripts[0]!.slideVersion).toBe(2);
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(trigger);
  await act(async () => trigger.click());
  await act(async () =>
    document.activeElement!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    ),
  );
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  await act(async () => trigger.click());
  await act(async () => document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })));
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
});
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    if (!this.open) return;
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
  state().reset();
  state().beginEntry('page-test');
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  vi.mocked(updatePitch).mockResolvedValue({ pitch_id: 'pitch-test', message: 'ok' });
  vi.mocked(createPitch).mockResolvedValue({ pitch_id: 'pitch-new', message: 'ok' });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it('한 페이지에 모든 입력을 두고 날짜 입력은 제거한다', async () => {
  await mount();
  expect(host.querySelectorAll('main > section')).toHaveLength(4);
  expect(host.textContent).not.toContain('발표 날짜');
  expect(host.querySelector('input[type="date"]')).toBeNull();
  expect(host.textContent).toContain('PDF를 여기에 끌어다 놓으세요');
  await act(async () => button('발표정보 확인하기 ↓')?.click());
  expect(host.querySelector('#create-info')).not.toBeNull();
});

it('현재 보고 있는 대본이 미저장이면 이전 저장 조합으로 시작하지 않는다', async () => {
  ready();
  state().addScriptVersion('[1]\n새 대본');
  await mount();
  expect(button('대본 연결 확인하기 ↓')).toBeDefined();
  await act(async () => button('대본 연결 확인하기 ↓').click());
  expect(host.querySelector('[data-testid="practice"]')).toBeNull();
});

it('미저장 제목은 저장 성공 뒤 최신 값과 서버 버전 ID로 점검에 넘긴다', async () => {
  ready();
  state().editInfo({ title: '고친 제목', timeLimitSec: 420 });
  state().select('info');
  await mount();
  await act(async () => button('저장하고 시작 전 점검 →').click());
  await vi.waitFor(() => expect(host.querySelector('[data-testid="practice"]')).not.toBeNull());
  expect(updatePitch).toHaveBeenCalledWith(
    'pitch-test',
    expect.objectContaining({
      title: '고친 제목',
      time_limit_sec: 420,
      presentation_date: '2026-11-01',
      lower_deviation: 30,
      upper_deviation: 60,
    }),
  );
  expect(host.textContent).toContain('"presentationVersionId":"pv1"');
  expect(host.textContent).toContain('"scriptVersionId":"sv1"');
  expect(host.textContent).toContain('"goalTimeSec":420');
});

it('저장 실패 시 입력을 보존하고 재시도 전에는 점검으로 이동하지 않는다', async () => {
  ready();
  state().editInfo({ title: '실패해도 남는 제목' });
  state().select('info');
  vi.mocked(updatePitch).mockRejectedValueOnce(new Error('연결 실패'));
  await mount();
  await act(async () => button('저장하고 시작 전 점검 →').click());
  await vi.waitFor(() => expect(host.textContent).toContain('발표정보를 저장하지 못했어요'));
  expect(state().infoEdits?.title).toBe('실패해도 남는 제목');
  expect(host.querySelector('[data-testid="practice"]')).toBeNull();
  await act(async () => button('저장하고 시작 전 점검 →').click());
  await vi.waitFor(() => expect(host.querySelector('[data-testid="practice"]')).not.toBeNull());
});

it('다른 섹션으로 이동해도 보고 있던 버전은 유지한다', () => {
  ready();
  state().select('slides', 1);
  state().select('script', 1);
  state().select('info');
  expect(state().viewedVersions).toMatchObject({ slides: 1, script: 1 });
});

it('새 발표의 두 편차는 화면과 생성 요청 모두 0초다', async () => {
  state().editInfo({ title: '새 발표' });
  await mount();
  expect(host.querySelector('#create-info')?.textContent?.match(/0초/g)).toHaveLength(2);
  expect(state().draft).toMatchObject({ lowerToleranceSec: 0, upperToleranceSec: 0 });
  await act(async () => button('발표정보 저장').click());
  await vi.waitFor(() =>
    expect(createPitch).toHaveBeenCalledWith(
      expect.objectContaining({
        lower_deviation: 0,
        upper_deviation: 0,
      }),
    ),
  );
});

it('불러온 발표의 시간 편차는 새 발표 기본값으로 덮어쓰지 않는다', async () => {
  const saved = {
    ...state().draft,
    title: '저장된 발표',
    lowerToleranceSec: 90,
    upperToleranceSec: 120,
  };
  state().hydrate('page-test', 'pitch-test', saved);
  await mount();
  expect(host.querySelector('#create-info')?.textContent).toContain('1분 30초');
  expect(state().draft).toMatchObject({ lowerToleranceSec: 90, upperToleranceSec: 120 });
});

it('자료 버전 관리에서는 선택만 제공하고 많은 버전도 전부 접근할 수 있다', async () => {
  ready();
  const original = state().draft.slides[0]!;
  useCreateStore.setState({
    draft: {
      ...state().draft,
      slides: Array.from({ length: 30 }, (_, i) => ({ ...original, version: i + 1 })),
    },
  });
  await mount();
  await act(async () => host.querySelector<HTMLElement>('.create-versions > summary')!.click());
  const rail = host.querySelector('.create-versions')!;
  expect(rail.textContent).not.toContain('발표 자료');
  expect(rail.textContent).not.toContain('발표정보');
  expect(rail.textContent).not.toContain('새 버전');
  expect([...rail.querySelectorAll('details')].every((el) => !el.open)).toBe(true);
  await act(async () => rail.querySelector<HTMLElement>('.create-version-tree summary')!.click());
  const before = state();
  const versions = rail.querySelectorAll<HTMLButtonElement>('button[aria-label^="슬라이드 V"]');
  expect(versions).toHaveLength(30);
  await act(async () => versions[29]!.click());
  expect(host.querySelector('dialog')?.textContent).toContain('슬라이드 V30 · 자료 보기');
  expect(state().version).toBe(before.version);
  expect(state().chosen).toEqual(before.chosen);
  expect(state().viewedVersions).toEqual(before.viewedVersions);
  expect(state().draft).toBe(before.draft);
  await act(async () => button('닫기').click());
  expect(host.querySelector('dialog')).toBeNull();
  expect(document.activeElement).toBe(versions[29]);
  await act(async () =>
    rail.querySelectorAll<HTMLElement>('.create-version-tree summary')[1]!.click(),
  );
  await act(async () =>
    rail.querySelector<HTMLButtonElement>('[aria-label="대본 V1 보기"]')!.click(),
  );
  expect(host.querySelector('dialog')?.textContent).toContain('발표 내용');
  expect(host.querySelector('dialog textarea')).toBeNull();
  await act(async () => button('닫기').click());
  expect(host.querySelector('#create-slides')?.textContent).toContain('+ 새 버전');
  expect(host.querySelector('#create-slides')?.textContent).not.toContain('파일 교체');
});

it('확인 창에서 장을 이동하고 편집해도 닫기와 재열기 후 원문·선택 조합을 보존한다', async () => {
  ready();
  useCreateStore.setState({
    draft: { ...state().draft, slides: [{ ...state().draft.slides[0]!, pageCount: 2 }] },
  });
  state().patchScript(1, { blocks: ['첫 장', '둘째 장'] });
  state().saveMapping(1);
  const chosen = state().chosen;
  await mount();
  const trigger = button('매핑 확인/수정');
  trigger.focus();
  await act(async () => trigger.click());
  expect(host.querySelector('dialog')?.open).toBe(true);
  expect(host.querySelectorAll('dialog textarea')).toHaveLength(1);
  button('닫기').focus();
  await act(async () =>
    button('닫기').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }),
    ),
  );
  expect(document.activeElement).toBe(button('다음 장'));
  await act(async () => button('다음 장').click());
  const textarea = host.querySelector<HTMLTextAreaElement>('dialog textarea')!;
  expect(textarea.getAttribute('aria-label')).toBe('슬라이드 2 대본');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
      textarea,
      '고친 둘째 장',
    );
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(state().draft.scripts[0]?.text).toContain('고친 둘째 장');
  expect(state().draft.scripts[0]?.saved).toBe(false);
  expect(button('저장').disabled).toBe(true);
  await act(async () => button('닫기').click());
  expect(host.querySelector('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(state().chosen).toEqual(chosen);
  await act(async () => trigger.click());
  await act(async () => button('다음 장').click());
  expect(host.querySelector<HTMLTextAreaElement>('dialog textarea')?.value).toBe('고친 둘째 장');
  await act(async () =>
    state().patchScript(1, {
      remote: { id: 'sv2', version: 2 },
      blocks: ['첫 장', '고친 둘째 장'],
      parse: { status: 'idle' },
    }),
  );
  await act(async () => button('저장').click());
  expect(state().draft.scripts[0]?.saved).toBe(true);
  expect(host.querySelector('dialog')?.textContent).toContain('저장 완료');
});
