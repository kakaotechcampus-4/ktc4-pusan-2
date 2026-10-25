// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { it, expect, vi } from 'vitest';
import { ConfirmPage } from './ConfirmPage';
import { apiRequest } from '@/shared/api/client';

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
