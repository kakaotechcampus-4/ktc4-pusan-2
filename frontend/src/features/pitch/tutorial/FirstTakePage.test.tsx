// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { FirstTakeError } from './firstTakeError';
import { FirstTakePage } from './FirstTakePage';
import { ensureFirstTakePitch } from './firstTakeSetup';

vi.mock('./firstTakeSetup', () => ({ ensureFirstTakePitch: vi.fn() }));

let host: HTMLDivElement;
let root: Root;
let unmounted = false;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  unmounted = false;
});

afterEach(async () => {
  if (!unmounted) await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/pitch/tutorial']}>
          <Routes>
            <Route path="/pitch/tutorial" element={<FirstTakePage />} />
            <Route path="/pitch/:pitchId/device-check" element={<div>장치 점검 진입</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

const start = () =>
  act(async () => {
    host.querySelector('button')!.click();
  });

it('시작 전에도 진행 상태 live region이 존재한다', async () => {
  await render();
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  expect(host.querySelector('[role="status"]')?.textContent).toBe('');
});

it('안내용 오류 메시지를 그대로 보여 주고 실패한 진행 상태를 지운다', async () => {
  vi.mocked(ensureFirstTakePitch).mockImplementation(async (progress) => {
    progress('슬라이드별 대본을 연결하고 있어요…');
    throw new FirstTakeError(
      '샘플 대본이 5개 슬라이드에 맞게 나뉘지 않았어요. 내 피치에서 대본을 확인해 주세요.',
    );
  });
  await render();
  await start();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(
    '내 피치에서 대본을 확인해 주세요.',
  );
  expect(host.querySelector('[role="status"]')?.textContent).toBe('');
});

it('일반 Error의 내부 정보는 숨기고 정해진 문구를 보여 준다', async () => {
  vi.mocked(ensureFirstTakePitch).mockRejectedValue(new TypeError('Failed to fetch'));
  await render();
  await start();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    '요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.',
  );
});

it('서버 ApiFailure는 기존 공통 오류 문구를 사용한다', async () => {
  vi.mocked(ensureFirstTakePitch).mockRejectedValue(new ApiFailure(404, 'SCRIPT_NOT_FOUND'));
  await render();
  await start();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    '대본을 찾을 수 없어요. 대본 매핑을 다시 눌러 주세요.',
  );
});

it('준비 중 중복 클릭은 API 호출 하나로 제한한다', async () => {
  let resolve!: (id: string) => void;
  vi.mocked(ensureFirstTakePitch).mockImplementation(
    () =>
      new Promise<string>((done) => {
        resolve = done;
      }),
  );
  await render();
  await start();
  expect(host.querySelector('button')?.getAttribute('disabled')).not.toBeNull();
  await start();
  expect(ensureFirstTakePitch).toHaveBeenCalledOnce();
  await act(async () => resolve('pitch-1'));
  expect(host.textContent).toContain('장치 점검 진입');
});

it('화면을 떠나면 준비 요청을 중단하고 이후 이동하지 않는다', async () => {
  let resolve!: (id: string) => void;
  let signal: AbortSignal | undefined;
  vi.mocked(ensureFirstTakePitch).mockImplementation((_progress, controllerSignal) => {
    signal = controllerSignal;
    return new Promise<string>((done) => {
      resolve = done;
    });
  });
  await render();
  await start();
  await act(async () => root.unmount());
  unmounted = true;
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve('pitch-1'));
  expect(host.textContent).not.toContain('장치 점검 진입');
});
