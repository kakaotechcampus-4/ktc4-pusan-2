// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LaunchCountdown, LaunchStart } from './LaunchCountdown';
import { LaunchGate } from '@/app/LaunchGate';
import { LAUNCH_AT } from '@/shared/api/launch';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.unstubAllGlobals();
});

async function show(status: object | null, guarded = false) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => {
      if (status === null) return Promise.reject(new Error('offline'));
      return Promise.resolve(Response.json(status));
    }),
  );
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/login']}>
          {guarded ? (
            <Routes>
              <Route
                path="/login"
                element={
                  <LaunchGate>
                    <p>로그인 화면</p>
                  </LaunchGate>
                }
              />
              <Route path="/welcome" element={<p>대기 페이지</p>} />
            </Routes>
          ) : (
            <>
              <LaunchCountdown />
              {[0, 1, 2].map((i) => (
                <LaunchStart key={i} className="">
                  시작하기
                </LaunchStart>
              ))}
            </>
          )}
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

it('공개 전 세 CTA를 비활성화하고 네 단위 타이머를 표시한다', async () => {
  await show({ server_now: '2026-10-29T05:00:00Z', launch_at: LAUNCH_AT, is_open: false });
  expect(container.querySelectorAll('button:disabled')).toHaveLength(3);
  expect(container.querySelector('[role="timer"]')?.textContent).toMatch(/일.*시간.*분.*초/);
  expect(container.querySelector('a')).toBeNull();
});

it('정각이 지나도 서버 미공개면 준비 중 상태를 유지한다', async () => {
  await show({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: false });
  expect(container.querySelector('[role="timer"]')).toBeNull();
  expect(container.querySelectorAll('button:disabled')).toHaveLength(3);
  expect(container.textContent).toContain('오픈 준비 중');
});

it('실제 공개되면 안내를 내리고 세 로그인 링크를 활성화한다', async () => {
  await show({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: true });
  expect(container.querySelector('section')).toBeNull();
  expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
});

it('조회 실패 시 재시도 안내를 표시하고 CTA를 열지 않는다', async () => {
  await show(null);
  expect(container.textContent).toContain('다시 시도');
  expect(container.querySelectorAll('button:disabled')).toHaveLength(3);
  expect(container.querySelector('a')).toBeNull();
});

it('미공개 상태에서는 로그인 화면을 렌더링하지 않고 대기 경로로 보낸다', async () => {
  await show({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: false }, true);
  expect(container.textContent).toBe('대기 페이지');
});
