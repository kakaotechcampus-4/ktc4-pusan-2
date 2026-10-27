// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { findSessionByTakeId } from '../lib/db';
import { RetryPage } from './RetryPage';

vi.mock('../lib/db', () => ({
  findSessionByTakeId: vi.fn(),
  getSession: vi.fn(),
  countAll: vi.fn(),
  readSlideChanges: vi.fn(),
}));
vi.mock('@/shared/api/take', () => ({ useEndTake: () => ({ isPending: false }) }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});
it('announces local record loading and then shows recovery navigation when no record exists', async () => {
  let resolve!: (value: null) => void;
  vi.mocked(findSessionByTakeId).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/takes/missing/retry']}>
        <Routes>
          <Route path="/takes/:takeId/retry" element={<RetryPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
  expect(host.querySelector('[role="status"]')?.textContent).toContain('발표 기록을 확인');
  await act(async () => resolve(null));
  expect(host.textContent).toContain('다시 보낼 발표 기록이 없어요');
  expect(host.querySelector('a[href="/"]')).not.toBeNull();
});
