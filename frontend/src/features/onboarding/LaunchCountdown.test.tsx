// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LaunchCountdown, LaunchStart } from './LaunchCountdown';
import { LaunchGate } from '@/app/LaunchGate';
import { LAUNCH_AT } from '@/shared/api/launch';

let container: HTMLDivElement;
let root: Root;
const launchTime = Date.parse(LAUNCH_AT);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(launchTime - 1000);
  vi.stubEnv('VITE_LAUNCH_GATE_ENABLED', 'true');
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  );
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  expect(fetch).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

async function show(guarded = false) {
  await act(async () => {
    root.render(
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
      </MemoryRouter>,
    );
  });
}

it('공개 전 세 CTA를 비활성화하고 네 단위 타이머를 표시한다', async () => {
  await show();
  expect(container.querySelectorAll('button:disabled')).toHaveLength(3);
  expect(container.querySelector('[role="timer"]')?.textContent).toMatch(/일.*시간.*분.*초/);
  expect(container.querySelector('a')).toBeNull();
});

it.each([0, 1])('예정 시각 +%s ms에 처음 방문하면 API 없이 열린다', async (offset) => {
  vi.setSystemTime(launchTime + offset);
  await show();
  expect(container.querySelector('section')).toBeNull();
  expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
});

it('화면을 보고 있어도 예정 시각에 안내가 사라지고 세 CTA가 열린다', async () => {
  await show();
  await act(async () => {
    vi.advanceTimersByTime(999);
  });
  expect(container.querySelectorAll('button:disabled')).toHaveLength(3);
  await act(async () => {
    vi.advanceTimersByTime(1);
  });
  expect(container.querySelector('section')).toBeNull();
  expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
});

it.each(['visibilitychange', 'focus'])('%s 때 기기 시각으로 다시 계산한다', async (event) => {
  await show();
  vi.setSystemTime(launchTime + 60_000);
  await act(async () => {
    (event === 'focus' ? window : document).dispatchEvent(new Event(event));
  });
  expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
});

it('공개 전 로그인 화면은 대기 경로로 보낸다', async () => {
  await show(true);
  expect(container.textContent).toBe('대기 페이지');
});

it('공개 시각 이후 로그인 경로를 허용한다', async () => {
  vi.setSystemTime(launchTime);
  await show(true);
  expect(container.textContent).toBe('로그인 화면');
});

it.each(['false', undefined, '', 'TRUE'])(
  '설정이 %s이면 카운트다운을 숨기고 기존 진입을 허용한다',
  async (value) => {
    vi.stubEnv('VITE_LAUNCH_GATE_ENABLED', value);
    await show();
    expect(container.querySelector('section')).toBeNull();
    expect(container.querySelectorAll('a[href="/login?next=/"]')).toHaveLength(3);
    expect(vi.getTimerCount()).toBe(0);
    await show(true);
    expect(container.textContent).toBe('로그인 화면');
  },
);
