// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { TakeHistoryRow } from '@/types/takeHistory';
import { TakesPage } from './TakesPage';
import { TakeComparisonRedirect } from './TakeComparisonRedirect';

const history = vi.hoisted(() => ({ data: { takes: [] as TakeHistoryRow[] } }));
const useHistory = vi.hoisted(() => vi.fn(() => history));
vi.mock('./api/takeHistory', () => ({ useTakeHistory: useHistory }));

function take(
  n: number,
  pitchId = 'p1',
  status: TakeHistoryRow['status'] = 'COMPLETED',
): TakeHistoryRow {
  return {
    take_id: pitchId + '-' + n,
    pitch_id: pitchId,
    pitch_title: pitchId === 'p1' ? '내 발표' : '다른 발표',
    take_number: n,
    started_at: '2026-10-06T12:00:00',
    status,
    end_reason: null,
    script_mode: 'HIGHLIGHT',
    duration_ms: 1000,
    goal_ms: 2000,
    audience_ratio: 0.5,
    script_ratio: 0.3,
    cpm: 300,
    filler_count: n,
    missions_achieved: 0,
    missions_total: 0,
    gaze_engine: 'current',
  };
}

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  useHistory.mockClear();
  history.data.takes = [take(2), take(1)];
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  router?.dispose();
  host.remove();
  vi.unstubAllGlobals();
});

async function render(path: string) {
  router = createMemoryRouter(
    [
      { path: '/takes', element: <TakesPage /> },
      { path: '/pitch/:id/comparison', element: <TakeComparisonRedirect /> },
    ],
    { initialEntries: [path] },
  );
  await act(async () => root.render(<RouterProvider router={router} />));
}

function view(label: string) {
  return [...host.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Take 기록 보기"] a')].find(
    (link) => link.textContent?.trim() === label,
  )!;
}

it.each([0, 1])('완료된 Take가 %i개면 비교 대신 추가 연습을 안내한다', async (count) => {
  history.data.takes = Array.from({ length: count }, (_, i) => take(i + 1));
  await render('/takes?view=compare&pitch=p1');
  expect(host.textContent).toContain('완료된 Take가 2개 이상 필요해요');
  expect(host.querySelector('table')).toBeNull();
  expect(view('지표 비교').getAttribute('aria-current')).toBe('page');
});

it('분석 중 기록은 목록에 남지만 비교 개수에 포함하지 않는다', async () => {
  history.data.takes = [take(3, 'p1', 'ANALYZING'), take(2), take(1)];
  await render('/takes?pitch=p1');
  expect(host.textContent).toContain('TAKE 3');
  await act(async () => view('지표 비교').click());
  expect(router.state.location.search).toContain('view=compare');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
});

it('같은 피치의 완료 Take만 비교해 다른 피치의 값을 혼합하지 않는다', async () => {
  history.data.takes = [take(7, 'p2'), take(2), take(1)];
  await render('/takes?pitch=p1&view=compare');
  const table = host.querySelector('table')!;
  expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
  expect(table.textContent).not.toContain('TAKE 7');
  const select = host.querySelector('select')!;
  await act(async () => {
    select.value = 'p2';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(router.state.location.search).toContain('pitch=p2');
  expect(host.querySelector('table')).toBeNull();
  expect(host.textContent).toContain('완료된 Take가 2개 이상 필요해요');
  expect(useHistory).toHaveBeenCalledWith();
});

it('목록 · 비교 선택과 피치 필터는 주소와 브라우저 뒤로 가기에 남는다', async () => {
  history.data.takes = [take(3, 'p2'), take(2), take(1)];
  await render('/takes?pitch=p1');
  expect(host.querySelector('select')?.value).toBe('p1');
  await act(async () => view('지표 비교').click());
  expect(router.state.location.search).toContain('pitch=p1');
  expect(view('지표 비교').getAttribute('aria-current')).toBe('page');
  await act(async () => {
    await router.navigate(-1);
  });
  expect(router.state.location.pathname).toBe('/takes');
  expect(host.querySelector('select')?.value).toBe('p1');
  expect(view('기록 목록').getAttribute('aria-current')).toBe('page');
  await act(async () => {
    await router.navigate(1);
  });
  expect(view('지표 비교').getAttribute('aria-current')).toBe('page');
});

it('옛 비교 링크는 쿼리와 해시를 보존하며 해당 피치의 비교 탭으로 replace 이동한다', async () => {
  await render('/pitch/p2/comparison?origin=report#metric');
  expect(router.state.location.pathname).toBe('/takes');
  const params = new URLSearchParams(router.state.location.search);
  expect(params.get('pitch')).toBe('p2');
  expect(params.get('view')).toBe('compare');
  expect(params.get('origin')).toBe('report');
  expect(router.state.location.hash).toBe('#metric');
  expect(view('지표 비교').getAttribute('aria-current')).toBe('page');
});

it('비교 화면의 지표 탭과 표는 키보드 접근 가능한 기존 컴포넌트를 사용한다', async () => {
  await render('/takes?pitch=p1&view=compare');
  expect(host.querySelectorAll('[role="tab"]')).toHaveLength(4);
  expect(host.querySelectorAll('[role="tab"][tabindex="0"]')).toHaveLength(1);
  expect(host.querySelector('[role="tabpanel"]')).not.toBeNull();
  expect(host.querySelector('[role="region"][tabindex="0"]')).not.toBeNull();
});

it('지표 비교 표의 각 완료 Take에서 자신에게 해당하는 리포트로 이동할 수 있다', async () => {
  history.data.takes = [take(7, 'p2'), take(2), take(1)];
  await render('/takes?pitch=p1&view=compare');
  const links = [...host.querySelectorAll<HTMLAnchorElement>('tbody a[href]')];
  expect(links.map((link) => link.getAttribute('href'))).toEqual(['/takes/p1-2', '/takes/p1-1']);
  expect(links.map((link) => link.getAttribute('aria-label'))).toEqual([
    'Take 2 리포트 보기',
    'Take 1 리포트 보기',
  ]);
  expect(host.querySelector('a[href="/takes/p2-7"]')).toBeNull();
});

it('비교 가능한 Take가 하나뿐이면 그 Take 리포트로 직접 이동할 수 있다', async () => {
  history.data.takes = [take(1, 'p1')];
  await render('/takes?pitch=p1&view=compare');
  expect(host.querySelector('a[href="/takes/p1-1"]')?.textContent).toContain('리포트 보기');
});

it('완료된 Take가 없으면 비교 화면에 리포트 링크를 만들지 않는다', async () => {
  history.data.takes = [take(2, 'p1', 'ANALYZING')];
  await render('/takes?pitch=p1&view=compare');
  expect(host.querySelector('a[href^="/takes/"]')).toBeNull();
});
