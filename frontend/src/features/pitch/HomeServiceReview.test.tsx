// @vitest-environment jsdom
import { act, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { HomeServiceReview } from './HomeServiceReview';
import { requestServiceReview } from '@/shared/api/serviceReview';
import { ApiFailure } from '@/shared/api/tokenStore';

const session = vi.hoisted(() => ({ data: { id: 'a' } as { id: string } | null }));
vi.mock('@/shared/api/useSession', () => ({ useSession: () => session }));
vi.mock('@/shared/api/serviceReview', () => ({ requestServiceReview: vi.fn() }));
vi.mock('./ServiceReviewDialog', () => ({
  ServiceReviewDialog: ({ onContinue }: { onContinue: () => void }) => (
    <div role="dialog">
      <button onClick={onContinue}>나중에</button>
    </div>
  ),
}));
const approved = {
  should_open: true,
  submitted: false,
  display_count: 3,
  display_limit: 3,
  reason: null,
};
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let client: QueryClient;
let router: ReturnType<typeof createMemoryRouter>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  session.data = { id: 'a' };
  vi.mocked(requestServiceReview).mockReset().mockResolvedValue(approved);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient();
  router = createMemoryRouter([
    { path: '*', element: <HomeServiceReview ready blocked={false} /> },
  ]);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router.dispose();
  client.clear();
  host.remove();
  vi.unstubAllGlobals();
});
async function render() {
  await act(async () =>
    root.render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </StrictMode>,
    ),
  );
}
async function settle() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
}
it('opens the third approval once; rerenders and focus do not count, a new visit does', async () => {
  await render();
  await settle();
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  expect(new Set(vi.mocked(requestServiceReview).mock.calls.map((c) => c[0])).size).toBe(1);
  await act(async () => host.querySelector('button')!.click());
  await render();
  window.dispatchEvent(new Event('focus'));
  await settle();
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  await act(async () => router.navigate('/me'));
  await act(async () => router.navigate('/'));
  await settle();
  expect(new Set(vi.mocked(requestServiceReview).mock.calls.map((c) => c[0])).size).toBe(2);
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
});
it.each(['ALREADY_SUBMITTED', 'NO_REPORT', 'DISPLAY_LIMIT_REACHED'] as const)(
  'does not open on %s',
  async (reason) => {
    vi.mocked(requestServiceReview).mockResolvedValue({ ...approved, should_open: false, reason });
    await render();
    await settle();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  },
);
it('retries an uncertain approval with the same UUID and allows its third approval', async () => {
  vi.mocked(requestServiceReview)
    .mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValue(approved);
  await render();
  await act(async () => new Promise((resolve) => setTimeout(resolve, 1100)));
  const calls = vi.mocked(requestServiceReview).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[0]![0]).toBe(calls[1]![0]);
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
});
it('ignores a late approval for a previous account', async () => {
  let resolve!: (value: typeof approved) => void;
  vi.mocked(requestServiceReview).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await render();
  session.data = { id: 'b' };
  vi.mocked(requestServiceReview).mockRejectedValue(new ApiFailure(401, 'UNAUTHORIZED'));
  // A new route element forces the mocked session subscriber to render as a real session update would.
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  );
  await act(async () => resolve(approved));
  await settle();
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});
