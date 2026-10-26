// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { it, expect, vi } from 'vitest';
import { ConfirmPage } from './ConfirmPage';
import { apiRequest, postJson } from '@/shared/api/client';
import { ApiFailure } from '@/shared/api/tokenStore';

vi.mock('@/shared/api/client', () => ({ apiRequest: vi.fn(), postJson: vi.fn() }));
vi.mock('@/shared/api/report', () => ({ useTakeReport: () => ({ data: undefined }) }));

it.each([false, true])(
  'keeps a selected answer after reconnect (refetch fails: %s)',
  async (fails) => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    const response = {
      take_id: 't1',
      analysis_stage: 'AWAITING_CONFIRMATION',
      remaining_ms: 600000,
      items: [
        {
          slide_number: 1,
          item_id: 'R1',
          kind: 'number',
          script_text: '37%',
          stt_text: '47%',
          script_sentence: null,
          stt_sentence: null,
        },
      ],
    };
    const request = vi.mocked(apiRequest);
    request.mockReset();
    request.mockResolvedValue(response);
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(async () =>
        root.render(
          <QueryClientProvider client={query}>
            <MemoryRouter initialEntries={['/takes/t1/confirm']}>
              <Routes>
                <Route path="/takes/:takeId/confirm" element={<ConfirmPage />} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>,
        ),
      );
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      const radio = container.querySelector<HTMLInputElement>('input[value="as_script"]')!;
      expect(radio).not.toBeNull();
      await act(async () => radio.click());
      expect(radio.checked).toBe(true);
      let finish!: (value: typeof response) => void;
      let fail!: (error: Error) => void;
      request.mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            finish = resolve;
            fail = reject;
          }),
      );
      await act(async () => {
        onlineManager.setOnline(false);
      });
      await act(async () => {
        onlineManager.setOnline(true);
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      expect(request).toHaveBeenCalledTimes(2);
      await act(async () => {
        if (fails) fail(new Error('offline'));
        else finish(response);
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      if (fails) {
        expect(container.textContent).toContain('확인할 말을 불러오지 못했어요.');
        const retry = [...container.querySelectorAll('button')].find(
          (button) => button.textContent === '다시 불러오기',
        )!;
        await act(async () => {
          retry.click();
          await new Promise((resolve) => setTimeout(resolve, 30));
        });
      }
      expect(container.querySelector<HTMLInputElement>('input[value="as_script"]')!.checked).toBe(
        true,
      );
    } finally {
      await act(async () => root.unmount());
      query.clear();
      container.remove();
      onlineManager.setOnline(true);
      vi.unstubAllGlobals();
    }
  },
);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

async function withSubmission(
  run: (context: {
    container: HTMLDivElement;
    query: QueryClient;
    response: ReturnType<typeof confirmationResponse>;
    submission: ReturnType<typeof deferred<{ analysis_stage: string }>>;
  }) => Promise<void>,
) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const response = confirmationResponse();
  vi.mocked(apiRequest).mockReset().mockResolvedValue(response);
  const submission = deferred<{ analysis_stage: string }>();
  vi.mocked(postJson).mockReset().mockReturnValue(submission.promise);
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(
        <QueryClientProvider client={query}>
          <MemoryRouter initialEntries={['/takes/t1/confirm']}>
            <Routes>
              <Route path="/takes/:takeId/confirm" element={<ConfirmPage />} />
              <Route path="/takes/:takeId/processing" element={<p>processing destination</p>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await settle();
    });
    await act(async () => {
      container.querySelector<HTMLInputElement>('input[value="as_script"]')!.click();
    });
    await act(async () => {
      [...container.querySelectorAll('button')].find((b) => b.textContent === '답 보내기')!.click();
      await settle();
    });
    expect(postJson).toHaveBeenCalledExactlyOnceWith('/api/takes/t1/confirmations', {
      answers: [{ slide_number: 1, item_id: 'R1', answer: 'as_script' }],
    });
    await run({ container, query, response, submission });
  } finally {
    await act(async () => root.unmount());
    query.clear();
    container.remove();
    onlineManager.setOnline(true);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
}

function confirmationResponse() {
  return {
    take_id: 't1',
    analysis_stage: 'AWAITING_CONFIRMATION',
    remaining_ms: 1000,
    items: [
      {
        slide_number: 1,
        item_id: 'R1',
        kind: null,
        script_text: '37%',
        stt_text: '47%',
        script_sentence: null,
        stt_sentence: null,
      },
    ],
  };
}

it.each([false, true])(
  'keeps submission pending across reconnect and handles its result (fails: %s)',
  async (fails) => {
    await withSubmission(async ({ container, response, submission }) => {
      const refresh = deferred<typeof response>();
      vi.mocked(apiRequest).mockReturnValueOnce(refresh.promise);
      await act(async () => {
        onlineManager.setOnline(false);
      });
      await act(async () => {
        onlineManager.setOnline(true);
        await settle();
      });
      expect(apiRequest).toHaveBeenCalledTimes(2);
      expect(container.textContent).toContain('답을 반영하는 중');
      await act(async () => {
        refresh.resolve(response);
        await settle();
      });
      expect(container.textContent).toContain('답을 반영하는 중');
      expect(container.querySelector('button')).toBeNull();
      expect(postJson).toHaveBeenCalledTimes(1);
      await act(async () => {
        if (fails) submission.reject(new ApiFailure(502, 'COVERAGE_RESCORE_FAILED'));
        else submission.resolve({ analysis_stage: 'REVIEWING' });
        await settle();
      });
      if (fails) {
        expect(container.textContent).toContain('다시 보내');
        expect(container.querySelector<HTMLInputElement>('input[value="as_script"]')!.checked).toBe(
          true,
        );
      } else expect(container.textContent).toContain('processing destination');
    });
  },
);

it.each([false, true])(
  'waits for the server past the local deadline (closed: %s)',
  async (closed) => {
    await withSubmission(async ({ container, submission }) => {
      const current = performance.now();
      vi.spyOn(performance, 'now').mockReturnValue(current + 2000);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 1100));
      });
      expect(container.textContent).toContain('답을 반영하는 중');
      expect(container.textContent).not.toContain('답 없이 그대로 진행');
      await act(async () => {
        if (closed) submission.reject(new ApiFailure(409, 'CONFIRMATION_CLOSED'));
        else submission.resolve({ analysis_stage: 'REVIEWING' });
        await settle();
      });
      expect(container.textContent).toContain(
        closed ? '확인을 받을 수 없어요' : 'processing destination',
      );
    });
  },
);
