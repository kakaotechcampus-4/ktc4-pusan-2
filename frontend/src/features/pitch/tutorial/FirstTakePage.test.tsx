// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { pitchListKey } from '@/shared/api/home';
import { startFirstTake } from '@/shared/api/pitch';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { FirstTakeResponse } from '@/types/pitch';
import { FirstTakePage } from './FirstTakePage';

vi.mock('@/shared/api/pitch', () => ({ startFirstTake: vi.fn() }));

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

async function render() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/pitch/tutorial']}>
          <Routes>
            <Route path="/pitch/tutorial" element={<FirstTakePage />} />
            <Route path="*" element={null} />
          </Routes>
          <Where />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

const where = () => host.querySelector('[data-testid="where"]')?.textContent;
// 화면의 버튼은 시작 버튼 하나입니다. 준비 중에는 글자가 바뀌어 글자로 찾지 않습니다
const startButton = () => host.querySelector('button')!;
// 쿼리 라이브러리는 결과를 setTimeout(0) 뒤에 알립니다. act 는 그 타이머까지 기다리지 않습니다
const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
const start = async () => {
  await act(async () => {
    startButton().click();
  });
  await flush();
};
const pending = () => {
  let resolve!: (res: FirstTakeResponse) => void;
  vi.mocked(startFirstTake).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  return async (res: FirstTakeResponse) => {
    await act(async () => resolve(res));
    await flush();
  };
};

it('시작 전에도 진행 상태 live region 이 있고 비어 있다', async () => {
  await render();
  expect(host.querySelector('[role="status"]')?.textContent).toBe('');
});

it('샘플 피치를 받으면 장치 점검으로 가고 홈 목록을 다시 받게 한다', async () => {
  vi.mocked(startFirstTake).mockResolvedValue({ pitch_id: 'demo-1', report_received: false });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  await render();
  await start();
  expect(startFirstTake).toHaveBeenCalledOnce();
  expect(where()).toBe('/pitch/demo-1/device-check');
  expect(invalidate).toHaveBeenCalledWith({ queryKey: pitchListKey });
});

it('튜토리얼 리포트를 이미 받았으면 장치 점검으로 가지 않고 새 피치 · 샘플 기록으로 안내한다', async () => {
  vi.mocked(startFirstTake).mockResolvedValue({ pitch_id: 'demo-1', report_received: true });
  await render();
  await start();
  expect(where()).toBe('/pitch/tutorial');
  const notice = host.querySelector('[aria-labelledby="first-take-used"]');
  expect(notice?.textContent).toContain('튜토리얼 리포트를 이미 받았어요');
  const hrefs = [...(notice?.querySelectorAll('a') ?? [])].map((a) => a.getAttribute('href'));
  expect(hrefs).toEqual(['/pitch/new', '/takes?pitch=demo-1']);
});

it('BE 에러 코드는 공통 문구로 알리고 다시 누를 수 있다', async () => {
  vi.mocked(startFirstTake).mockRejectedValueOnce(new ApiFailure(409, 'TUTORIAL_PITCH_BUSY'));
  await render();
  await start();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    '샘플 발표를 준비하지 못했어요. 샘플 발표를 준비하고 있어요. 잠시 뒤 다시 시도해 주세요.',
  );
  expect(startButton().getAttribute('aria-disabled')).toBe('false');

  vi.mocked(startFirstTake).mockResolvedValueOnce({ pitch_id: 'demo-1', report_received: false });
  await start();
  expect(where()).toBe('/pitch/demo-1/device-check');
});

it('네트워크 오류의 내부 문구는 보이지 않는다', async () => {
  vi.mocked(startFirstTake).mockRejectedValue(new TypeError('Failed to fetch'));
  await render();
  await start();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    '샘플 발표를 준비하지 못했어요. 요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.',
  );
  expect(host.textContent).not.toContain('Failed to fetch');
});

it('준비 중에는 버튼을 aria-disabled 로 막아 초점은 두고 요청은 한 번만 보낸다', async () => {
  const resolve = pending();
  await render();
  await start();
  expect(startButton().getAttribute('aria-disabled')).toBe('true');
  expect(startButton().hasAttribute('disabled')).toBe(false);
  expect(host.querySelector('[role="status"]')?.textContent).toBe('샘플 발표를 준비하고 있어요.');
  await start();
  expect(startFirstTake).toHaveBeenCalledOnce();
  await resolve({ pitch_id: 'demo-1', report_received: false });
  expect(where()).toBe('/pitch/demo-1/device-check');
});

it('준비 중에 화면을 떠나면 응답이 와도 장치 점검으로 끌고 가지 않는다', async () => {
  const resolve = pending();
  await render();
  await start();
  const back = [...host.querySelectorAll('a')].find((a) => a.textContent === '내 피치로 돌아가기')!;
  await act(async () => back.click());
  expect(where()).toBe('/');
  await resolve({ pitch_id: 'demo-1', report_received: false });
  expect(where()).toBe('/');
});
