// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiRequest } from './client';
import { usePrepare } from './prepare';

vi.mock('./client', () => ({ apiRequest: vi.fn(), postJson: vi.fn() }));

let host: HTMLDivElement;
let root: Root;
let seen: (string | undefined)[];

const flush = () => act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

function Probe() {
  const { data } = usePrepare('p1');
  seen.push((data as { title?: string } | undefined)?.title);
  return null;
}

beforeEach(() => {
  seen = [];
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.mocked(apiRequest).mockReset();
});

it('장치 점검에 다시 들어오면 남겨 둔 답 대신 서버에 새로 묻는다', async () => {
  // 앱과 같은 기본값 — 받은 답을 30초 동안 새것으로 봅니다 (main.tsx)
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  vi.mocked(apiRequest)
    .mockResolvedValueOnce({ title: '수정 전' })
    .mockResolvedValueOnce({ title: '수정 후' });
  const page = (on: boolean) => (
    <QueryClientProvider client={client}>{on && <Probe />}</QueryClientProvider>
  );

  await act(async () => root.render(page(true)));
  await flush();
  expect(seen.at(-1)).toBe('수정 전');

  // 피치 수정으로 나갔다가 30초 안에 돌아옵니다
  await act(async () => root.render(page(false)));
  await flush();
  seen = [];
  await act(async () => root.render(page(true)));
  await flush();

  expect(apiRequest).toHaveBeenCalledTimes(2);
  expect(seen[0]).toBeUndefined();
  expect(seen.at(-1)).toBe('수정 후');
});

it('장치 점검을 띄워 둔 채 다른 탭에서 고치고 돌아오면 새로 받는다', async () => {
  // 앱과 같은 기본값 — 탭으로 돌아와도 다시 받지 않습니다 (main.tsx)
  const client = new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: false, refetchOnWindowFocus: false },
    },
  });
  vi.mocked(apiRequest)
    .mockResolvedValueOnce({ title: '수정 전' })
    .mockResolvedValueOnce({ title: '수정 후' });

  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>,
    ),
  );
  await flush();
  expect(seen.at(-1)).toBe('수정 전');

  try {
    await act(async () => focusManager.setFocused(false));
    await act(async () => focusManager.setFocused(true));
    await flush();
  } finally {
    focusManager.setFocused(undefined);
  }

  expect(apiRequest).toHaveBeenCalledTimes(2);
  expect(seen.at(-1)).toBe('수정 후');
});
