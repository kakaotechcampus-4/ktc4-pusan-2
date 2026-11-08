// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MyPage } from './MyPage';

vi.mock('./useSession', () => ({
  useSession: () => ({
    data: { id: 'u1', name: '테스트', email: 'test@example.com', raw_data_consent: false },
  }),
}));
vi.mock('./SuggestionForm', () => ({ SuggestionForm: () => null }));
vi.mock('./api/useWithdraw', () => ({
  useWithdraw: () => ({ isPending: false, isError: false, mutate: vi.fn() }),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () =>
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <MyPage />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

// `getByLabelText` 와 같은 방식으로 레이블의 연결된 컨트롤을 찾습니다.
// 레이블 텍스트가 바뀌거나 `htmlFor` · `id` 가 어긋나면 여기서 실패합니다.
function consentCheckbox() {
  const label = Array.from(host.querySelectorAll('label')).find((el) =>
    el.textContent?.includes('연습 기록을 남겨 다시 분석하는 데 동의해요'),
  );
  expect(label).toBeDefined();
  expect(label!.htmlFor).toBe('raw-data-consent');
  const control = label!.control;
  expect(control).toBeInstanceOf(HTMLInputElement);
  expect((control as HTMLInputElement).type).toBe('checkbox');
  return control as HTMLInputElement;
}

it('links the consent checkbox to its label with for · id', () => {
  const checkbox = consentCheckbox();
  expect(checkbox.id).toBe('raw-data-consent');
  expect(checkbox.checked).toBe(false);
});
