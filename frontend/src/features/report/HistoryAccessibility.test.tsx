// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { TakeHistoryRow } from '@/types/takeHistory';
import { ComparisonPage } from './ComparisonPage';
import { TakesPage } from './TakesPage';

const query = vi.hoisted(() => ({ data: { takes: [] as TakeHistoryRow[] } }));
vi.mock('@/shared/api/takeHistory', () => ({ useTakeHistory: () => query }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  query.data.takes = [3, 2, 1].map((n) => ({
    take_id: `t${n}`,
    pitch_id: n === 1 ? 'p2' : 'p1',
    pitch_title: n === 1 ? '다른 발표' : '내 발표',
    take_number: n,
    started_at: '2026-10-06T12:00:00',
    status: 'COMPLETED',
    script_mode: 'HIGHLIGHT',
    duration_ms: 1000,
    goal_ms: 2000,
    audience_ratio: 0.5,
    script_ratio: 0.3,
    cpm: 300,
    filler_count: n,
    missions_achieved: 0,
    missions_total: 0,
    gaze_engine: n === 1 ? 'old' : 'current',
  }));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function render(page: 'compare' | 'takes') {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/pitch/p1/comparison']}>
        <Routes>
          <Route
            path="/pitch/:id/comparison"
            element={page === 'compare' ? <ComparisonPage /> : <TakesPage />}
          />
        </Routes>
      </MemoryRouter>,
    ),
  );
}
it('moves tab focus and selection together, with a named linked panel and one tab stop', async () => {
  await render('compare');
  const tabs = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  await act(async () => tabs[0]!.focus());
  for (const [key, index] of [
    ['ArrowLeft', 3],
    ['ArrowRight', 0],
    ['End', 3],
    ['Home', 0],
    ['ArrowRight', 1],
  ] as const) {
    await act(async () =>
      document.activeElement!.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
      ),
    );
    expect(document.activeElement).toBe(tabs[index]);
    expect(tabs[index]!.getAttribute('aria-selected')).toBe('true');
    expect(host.querySelectorAll('[role="tab"][tabindex="0"]')).toHaveLength(1);
    const panel = host.querySelector('[role="tabpanel"]')!;
    expect(panel.id).toBe(tabs[index]!.getAttribute('aria-controls'));
    expect(panel.getAttribute('aria-labelledby')).toBe(tabs[index]!.id);
  }
});
it('keeps old-engine gaze values excluded while allowing other metrics and keyboard table access', async () => {
  await render('compare');
  const firstBar = () => host.querySelector('[role="tabpanel"] ol li')!;
  expect(firstBar().textContent).toBe('—');
  await act(async () => host.querySelector<HTMLButtonElement>('[role="tab"]:last-child')!.click());
  expect(firstBar().textContent).toBe('1회');
  expect(host.querySelector('[role="region"]')?.getAttribute('tabindex')).toBe('0');
});
it('filters by pitch and announces the result count without losing report destinations', async () => {
  await render('takes');
  const select = host.querySelector('select')!;
  await act(async () => {
    select.value = 'p2';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(host.querySelector('[role="status"]')?.textContent).toBe('연습 기록 1개');
  expect(host.querySelector('a[href="/takes/t1"]')?.getAttribute('aria-label')).toBe(
    '다른 발표 Take 1 리포트',
  );
  expect(host.querySelector('a[href="/takes/t2"]')).toBeNull();
});
