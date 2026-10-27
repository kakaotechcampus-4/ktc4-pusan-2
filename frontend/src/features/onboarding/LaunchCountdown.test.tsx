// @vitest-environment jsdom
import { act, useEffect } from 'react';
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
  vi.stubGlobal('location', new URL('https://pitch-coach-release.duckdns.org'));
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

it('최초 공개 상태 조회에 실패하면 보호 화면을 열지 않는다', async () => {
  await show(null, true);
  expect(container.textContent).toBe('대기 페이지');
});

it.each(['pitch-coach-dev.duckdns.org', 'localhost', '127.0.0.1'])(
  '%s에서는 상태 API 없이 카운트다운을 숨기고 CTA와 보호 화면을 연다',
  async (hostname) => {
    vi.stubGlobal('location', new URL(`https://${hostname}`));
    await show(null);
    expect(container.querySelector('section')).toBeNull();
    expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
    expect(fetch).not.toHaveBeenCalled();

    await show(null, true);
    expect(container.textContent).toBe('로그인 화면');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
      await client.invalidateQueries({ queryKey: ['launch-status'] });
    });
    expect(fetch).not.toHaveBeenCalled();
  },
);

it('공개 후 재조회 실패는 진행 중인 화면을 유지하고 명시적인 비공개 응답은 차단한다', async () => {
  const cleanup = vi.fn();
  function RunningSession() {
    useEffect(() => cleanup, []);
    return <p>진행 중인 발표</p>;
  }

  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: true }),
      ),
  );
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/rehearsal']}>
          <Routes>
            <Route
              path="/rehearsal"
              element={
                <LaunchGate>
                  <RunningSession />
                </LaunchGate>
              }
            />
            <Route path="/welcome" element={<p>대기 페이지</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  expect(container.textContent).toBe('진행 중인 발표');

  vi.mocked(fetch).mockRejectedValue(new Error('temporary outage'));
  await act(async () => {
    await client.refetchQueries({ queryKey: ['launch-status'] });
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  expect(client.getQueryState(['launch-status'])?.status).toBe('error');
  expect(container.textContent).toBe('진행 중인 발표');
  expect(cleanup).not.toHaveBeenCalled();

  vi.mocked(fetch).mockResolvedValue(
    Response.json({ server_now: LAUNCH_AT, launch_at: LAUNCH_AT, is_open: false }),
  );
  await act(async () => {
    await client.refetchQueries({ queryKey: ['launch-status'] });
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  expect(container.textContent).toBe('대기 페이지');
  expect(cleanup).toHaveBeenCalledOnce();
});
