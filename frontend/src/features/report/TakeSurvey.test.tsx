// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { TakeSurveyState } from '@/types/takeSurvey';
import { TakeSurvey } from './TakeSurvey';

// 서버 대신 요청만 받아 둡니다. GET 은 지금 상태를, POST 는 테스트가 정한 결과를 돌려줍니다
const api = vi.hoisted(() => ({
  state: { submitted: false, survey: null } as TakeSurveyState,
  post: vi.fn(),
}));
vi.mock('@/shared/api/client', () => ({
  apiRequest: vi.fn(async () => api.state),
  postJson: (path: string, body: unknown) => api.post(path, body),
}));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  // jsdom 에는 showModal · close 가 없습니다 — 열림 표시만 흉내 냅니다
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  api.state = { submitted: false, survey: null };
  api.post.mockReset();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render(completed = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <TakeSurvey takeId="t1" completed={completed} />
      </QueryClientProvider>,
    ),
  );
  await flush();
}
// React Query 는 결과를 setTimeout 한 번 뒤에 알립니다
const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  });
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === text);
const choose = async (name: string, value: number) =>
  act(async () =>
    host.querySelector<HTMLInputElement>(`input[name="${name}"][value="${value}"]`)!.click(),
  );
const type = async (text: string) =>
  act(async () => {
    const box = host.querySelector('textarea')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
    setter.call(box, text);
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });

it('분석이 끝나지 않은 리포트에는 설문 버튼을 두지 않는다', async () => {
  await render(false);
  expect(host.textContent).toBe('');
});

it('두 점수를 고르면 보내고, 보낸 뒤에는 보냈다고 알린다', async () => {
  await render();
  await act(async () => button('이번 분석 설문 남기기')!.click());
  expect(button('설문 보내기')!.disabled).toBe(true);

  await choose('accuracy', 4);
  await choose('usefulness', 3);
  await type('  말 빠르기가 느리게 나왔어요  ');
  api.post.mockResolvedValue({
    take_id: 't1',
    accuracy_rating: 4,
    usefulness_rating: 3,
    comment: '말 빠르기가 느리게 나왔어요',
    question_version: 1,
    created_at: '2026-10-30T00:00:00Z',
  });
  await act(async () => button('설문 보내기')!.click());
  await flush();

  expect(api.post).toHaveBeenCalledWith('/api/takes/t1/survey', {
    accuracy_rating: 4,
    usefulness_rating: 3,
    comment: '말 빠르기가 느리게 나왔어요',
    question_version: 1,
  });
  expect(host.querySelector('dialog')).toBeNull();
  expect(host.querySelector('[role="status"]')?.textContent).toContain('설문을 보냈어요');
});

it('보내지 못하면 입력을 남겨 두고 같은 답으로 다시 보내게 한다', async () => {
  await render();
  await act(async () => button('이번 분석 설문 남기기')!.click());
  await choose('accuracy', 5);
  await choose('usefulness', 5);
  await type('좋았어요');
  api.post.mockRejectedValueOnce(new ApiFailure(500, 'INTERNAL_SERVER_ERROR'));
  await act(async () => button('설문 보내기')!.click());
  await flush();

  expect(host.querySelector('[role="alert"]')?.textContent).toContain('보내지 못했어요');
  expect(host.querySelector('textarea')!.value).toBe('좋았어요');
  expect(button('다시 보내기')).toBeDefined();
});

it('다른 곳에서 먼저 보냈으면 서버 상태로 바꿔 보냈다고 알린다', async () => {
  await render();
  await act(async () => button('이번 분석 설문 남기기')!.click());
  await choose('accuracy', 2);
  await choose('usefulness', 2);
  api.post.mockRejectedValueOnce(new ApiFailure(409, 'TAKE_SURVEY_ALREADY_SUBMITTED'));
  api.state = { submitted: true, survey: null };
  await act(async () => button('설문 보내기')!.click());
  await flush();

  expect(host.querySelector('dialog')).toBeNull();
  expect(host.querySelector('[role="status"]')?.textContent).toContain('이미 보냈어요');
});

it('이미 낸 리포트는 버튼 대신 보냈다는 줄만 둔다', async () => {
  api.state = { submitted: true, survey: null };
  await render();
  expect(button('이번 분석 설문 남기기')).toBeUndefined();
  expect(host.textContent).toContain('설문을 보냈어요');
});
