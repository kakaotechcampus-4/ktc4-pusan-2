// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReportPage } from './ReportPage';
import { measuresFixture, reviewFixture } from '@/mocks/report';
import type { TakeReport } from '@/types/report';

const query = vi.hoisted(() => ({
  data: undefined as TakeReport | undefined,
  error: null as Error | null,
  isPending: false,
}));
vi.mock('@/shared/api/report', () => ({ useTakeReport: () => query }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  // jsdom does not implement native dialog methods. Define own properties so
  // the test double can be removed without leaking into other tests.
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    },
  });
  query.error = null;
  query.data = {
    take_id: 't3',
    pitch_id: 'p1',
    pitch_title: '연습',
    take_number: 3,
    script_mode: 'HIGHLIGHT',
    ended_at: '2026-10-26T00:00:00Z',
    status: 'COMPLETED',
    analysis_stage: 'DONE',
    review_status: 'DONE',
    analysis_hold_reason: null,
    measures: measuresFixture,
    review: reviewFixture,
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
});
async function render() {
  await act(async () =>
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/takes/t3']}>
          <Routes>
            <Route path="/takes/:takeId" element={<ReportPage />} />
            <Route path="/" element={<p>홈 도착</p>} />
            <Route path="/pitch/p1/device-check" element={<p>장치 점검 도착</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}
it('shows the previous Take comparison immediately after the summary and before measures', async () => {
  await render();
  const main = host.querySelector('main')!;
  const summary = main.querySelector('[role="status"]')!.closest('section')!;
  const comparison = summary.nextElementSibling!;
  expect(comparison.querySelector('h2')?.textContent).toBe('지난 Take와 비교');
  expect(comparison.nextElementSibling?.getAttribute('aria-label')).toBe('측정값');
  const pageHeadings = [...main.querySelectorAll('h2')].filter(
    (heading) => !heading.closest('dialog'),
  );
  expect(pageHeadings).toHaveLength(8);
});

it('shows the script modes in Korean when the two Takes used different ones', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'script_ratio',
        slide_number: null,
        previous: 0.7,
        current: 0.62,
        improved: true,
        script_mode: { previous: 'HIGHLIGHT', current: 'OFF' },
      },
    ],
  };
  await render();
  const text = host.querySelector('main')!.textContent;
  expect(text).toContain('전체 대본 + 하이라이트 → 대본 없이');
  expect(text).not.toContain('HIGHLIGHT');
});

it('shows 변화 없음, not 악화, when the visible values are equal even if improved is false', async () => {
  const row = { script_mode: null };
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        ...row,
        metric: 'filler_count',
        slide_number: null,
        previous: 0,
        current: 0,
        improved: false,
      },
      {
        ...row,
        metric: 'script_ratio',
        slide_number: null,
        previous: 0.621,
        current: 0.624,
        improved: false,
      },
      { ...row, metric: 'filler_count', slide_number: 2, previous: 0, current: 2, improved: false },
    ],
  };
  await render();
  await openComparisonDetail();
  const cell = (dimension: string, row: number) =>
    host
      .querySelector('dialog')!
      .querySelector(`section[aria-label="${dimension}"] tbody tr:nth-of-type(${row}) td`)!
      .textContent;
  expect(cell('대본에서 벗어나기', 1)).toContain('변화 없음');
  expect(cell('군더더기 없이', 1)).toContain('변화 없음');
  expect(cell('군더더기 없이', 2)).toContain('악화 ↓');
});

async function openComparisonDetail() {
  const button = [...host.querySelectorAll('button')].find((b) =>
    b.textContent?.includes('상세 비교표 보기'),
  )!;
  await act(async () => button.click());
}

function comparisonCard() {
  return [...host.querySelectorAll('main section')].find(
    (s) => s.querySelector('h2')?.textContent === '지난 Take와 비교',
  )!;
}

it('lays the comparison out one column per metric and one row per slide, grouped by dimension (#929)', async () => {
  const row = { previous: 0.7, current: 0.62, improved: true, script_mode: null };
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      { ...row, metric: 'script_ratio', slide_number: null },
      { ...row, metric: 'script_ratio', slide_number: 3 },
      { ...row, metric: 'reading_runs', slide_number: null, previous: 4, current: 2 },
      { ...row, metric: 'filler_per_min', slide_number: null, previous: 2, current: 1 },
      { ...row, metric: 'filler_count', slide_number: null, previous: 10, current: 7 },
    ],
  };
  await render();
  await openComparisonDetail();
  const card = host.querySelector('dialog')!;
  const tables = [...card.querySelectorAll('section')].map((s) => ({
    title: s.querySelector('h3')?.textContent,
    columns: [...s.querySelectorAll('thead th')].map((th) => th.textContent),
    rows: [...s.querySelectorAll('tbody th')].map((th) => th.textContent),
  }));
  expect(tables).toEqual([
    {
      title: '대본에서 벗어나기',
      columns: ['구간', '대본 응시', '대본 읽기 횟수'],
      rows: ['발표 전체', '3번 장'],
    },
    { title: '군더더기 없이', columns: ['구간', '군더더기'], rows: ['발표 전체'] },
  ]);
  // 장 값이 없는 지표는 빈칸 대신 — 로
  expect(card.querySelector('tbody tr:nth-of-type(2) td:nth-of-type(2)')?.textContent).toBe('—');
  expect(card.textContent).not.toContain('분당 군더더기');
  expect(card.textContent).toContain('지난 Take → 이번 Take');
});

it('says once that the script modes differed instead of repeating it in every cell', async () => {
  const row = {
    previous: 0.7,
    current: 0.62,
    improved: true,
    script_mode: { previous: 'HIGHLIGHT', current: 'OFF' },
  };
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      { ...row, metric: 'script_ratio', slide_number: null },
      { ...row, metric: 'audience_ratio', slide_number: null },
    ],
  };
  await render();
  expect(comparisonCard().textContent!.match(/대본 보기가 달랐어요/g)).toHaveLength(1);
});

it('shows per-metric improved and worsened slides before opening details', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'audience_ratio',
        slide_number: 2,
        previous: 0.3,
        current: 0.5,
        improved: true,
        script_mode: null,
      },
      {
        metric: 'audience_ratio',
        slide_number: 3,
        previous: 0.6,
        current: 0.4,
        improved: false,
        script_mode: null,
      },
    ],
  };
  await render();
  const metric = [...comparisonCard().querySelectorAll('section')].find(
    (section) => section.querySelector('h3')?.textContent === '청중 응시',
  )!;
  const slideLists = metric.querySelectorAll('p.mt-1');
  expect(metric.textContent).toContain('개선된 슬라이드');
  expect(metric.textContent).toContain('다시 점검할 슬라이드');
  expect(slideLists[0]?.textContent).toBe('2번');
  expect(slideLists[1]?.textContent).toBe('3번');
  const improved = metric.querySelector('[role="group"][aria-label="개선된 슬라이드"]')!;
  const worsened = metric.querySelector('[role="group"][aria-label="다시 점검할 슬라이드"]')!;
  expect(improved.classList.contains('bg-coral-wash')).toBe(true);
  expect(worsened.classList.contains('bg-coral-wash')).toBe(false);
  await openComparisonDetail();
  expect(host.querySelector('dialog[open]')).not.toBeNull();
  expect(host.querySelector('dialog table')).not.toBeNull();
});

it('does not highlight an empty improvement group', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'audience_ratio',
        slide_number: 2,
        previous: 0.5,
        current: 0.3,
        improved: false,
        script_mode: null,
      },
    ],
  };
  await render();
  const improved = comparisonCard().querySelector('[role="group"][aria-label="개선된 슬라이드"]');
  expect(improved?.textContent).toContain('없음');
  expect(improved?.classList.contains('bg-coral-wash')).toBe(false);
});

it('keeps summary metrics ordered and hidden consistently with the detail table', async () => {
  const row = { previous: 0.7, current: 0.62, improved: true, script_mode: null };
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      { ...row, metric: 'filler_count', slide_number: null },
      { ...row, metric: 'filler_per_min', slide_number: null },
      { ...row, metric: 'script_ratio', slide_number: null },
      { ...row, metric: 'audience_ratio', slide_number: null },
    ],
  };
  await render();
  const headings = [...comparisonCard().querySelectorAll('section > h3')]
    .filter((heading) => !heading.closest('dialog'))
    .map((heading) => heading.textContent);
  expect(headings).toEqual(['청중 응시', '대본 응시', '군더더기']);
  await openComparisonDetail();
  const columns = [...host.querySelectorAll('dialog thead th:not(:first-child)')].map(
    (heading) => heading.textContent,
  );
  expect(columns).toEqual(headings);
});

it('describes metrics reported only for the whole Take without suggesting missing data', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'duration_ms',
        slide_number: null,
        previous: 150000,
        current: 140000,
        improved: true,
        script_mode: null,
      },
    ],
  };
  await render();
  const whole = comparisonCard().querySelector(
    '[role="group"][aria-label="발표 시간 발표 전체 비교"]',
  );
  expect(whole).not.toBeNull();
  expect(whole?.textContent).toContain('지난 Take');
  expect(whole?.textContent).toContain('이번 Take');
  expect(whole?.textContent).toContain('개선 ↑');
  expect(whole?.classList.contains('bg-coral-wash')).toBe(true);
  expect(whole?.classList.contains('border-coral-deep')).toBe(true);
  expect(comparisonCard().textContent).not.toContain('발표 전체 기준 지표예요.');
});

it.each([
  { improved: false, current: 140000, label: '악화 ↓' },
  { improved: null, current: 140000, label: '방향 판단 불가' },
  { improved: false, current: 150000, label: '변화 없음' },
])('keeps $label neutral for a whole-Take metric', async ({ improved, current, label }) => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'duration_ms',
        slide_number: null,
        previous: 150000,
        current,
        improved,
        script_mode: null,
      },
    ],
  };
  await render();
  const whole = comparisonCard().querySelector(
    '[role="group"][aria-label="발표 시간 발표 전체 비교"]',
  );
  expect(whole?.textContent).toContain(label);
  expect(whole?.classList.contains('bg-coral-wash')).toBe(false);
  expect(whole?.classList.contains('bg-panel')).toBe(true);
});

it('shows undetermined direction without falsely saying no slides improved or worsened', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'pause_count',
        slide_number: 2,
        previous: 1,
        current: 3,
        improved: null,
        script_mode: null,
      },
    ],
  };
  await render();
  const metric = [...comparisonCard().querySelectorAll('section')].find(
    (section) => section.querySelector('h3')?.textContent === '멈춤',
  )!;
  expect(metric.textContent).toContain('개선·악화 방향을 판단할 수 없는 지표예요.');
  expect(metric.textContent).not.toContain('개선된 슬라이드');
  expect(metric.textContent).not.toContain('다시 점검할 슬라이드');
});

it('does not label a metric with missing slide details as whole-Take-only', async () => {
  query.data!.review = {
    ...reviewFixture,
    comparison: [
      {
        metric: 'audience_ratio',
        slide_number: null,
        previous: 0.5,
        current: 0.7,
        improved: true,
        script_mode: null,
      },
    ],
  };
  await render();
  expect(comparisonCard().textContent).toContain('슬라이드별 비교 결과가 없어요.');
  expect(comparisonCard().textContent).not.toContain('발표 전체 기준 지표예요.');
});

it('restores focus after the detail dialog receives cancel and close events', async () => {
  await render();
  await openComparisonDetail();
  const dialog = host.querySelector('dialog')!;
  const trigger = [...host.querySelectorAll('button')].find((button) =>
    button.textContent?.includes('상세 비교표 보기'),
  )!;
  await act(async () => {
    const cancel = new Event('cancel', { cancelable: true });
    dialog.dispatchEvent(cancel);
    if (!cancel.defaultPrevented) dialog.close();
  });
  expect(dialog.hasAttribute('open')).toBe(false);
  expect(document.activeElement).toBe(trigger);
});

it('restores focus to the detail trigger after closing the dialog', async () => {
  await render();
  await openComparisonDetail();
  const trigger = [...host.querySelectorAll('button')].find((button) =>
    button.textContent?.includes('상세 비교표 보기'),
  )!;
  const closeButton = host.querySelector<HTMLButtonElement>(
    'dialog button[aria-label="상세 비교 닫기"]',
  )!;
  expect(document.body.style.overflow).toBe('hidden');
  await act(async () => closeButton.click());
  expect(host.querySelector('dialog[open]')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(document.body.style.overflow).toBe('');
});
