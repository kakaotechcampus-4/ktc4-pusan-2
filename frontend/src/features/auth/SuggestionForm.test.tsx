// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { SuggestionForm } from './SuggestionForm';
import { submitSuggestion } from '@/shared/api/serviceReview';
import { ApiFailure } from '@/shared/api/tokenStore';
vi.mock('@/shared/api/serviceReview', () => ({ submitSuggestion: vi.fn() }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  vi.mocked(submitSuggestion)
    .mockReset()
    .mockResolvedValue({ id: 'one', source: 'MY_PAGE', created_at: '2026-10-27T00:00:00Z' });
  await act(async () => root.render(<SuggestionForm key="a" />));
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function fill(value: string) {
  await act(async () => {
    const input = host.querySelector('textarea')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
      input,
      value,
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function send() {
  await act(async () =>
    host
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  );
}
it('requires 1–2000 characters, preserves the exact retry, then creates a new request for a new opinion', async () => {
  await fill('   ');
  await send();
  expect(submitSuggestion).not.toHaveBeenCalled();
  await fill('가'.repeat(2001));
  await send();
  expect(submitSuggestion).not.toHaveBeenCalled();
  await fill('  의견 🌱  ');
  vi.mocked(submitSuggestion).mockRejectedValueOnce(new Error('lost response'));
  await send();
  const first = vi.mocked(submitSuggestion).mock.calls[0]![0];
  expect(first.content).toBe('의견 🌱');
  expect(host.querySelector('textarea')!.value).toBe('  의견 🌱  ');
  expect(host.querySelector('textarea')!.readOnly).toBe(true);
  await send();
  expect(vi.mocked(submitSuggestion).mock.calls[1]![0]).toEqual(first);
  expect(host.querySelector('textarea')!.value).toBe('');
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  await fill('다음 의견');
  await send();
  expect(vi.mocked(submitSuggestion).mock.calls[2]![0].request_id).not.toBe(first.request_id);
});
it('keeps a conflicting request without silently generating a new UUID', async () => {
  vi.mocked(submitSuggestion).mockRejectedValue(new ApiFailure(409, 'SUGGESTION_REQUEST_CONFLICT'));
  await fill('의견');
  await send();
  await send();
  expect(vi.mocked(submitSuggestion).mock.calls[0]![0]).toEqual(
    vi.mocked(submitSuggestion).mock.calls[1]![0],
  );
  expect(host.querySelector('[role="alert"]')!.textContent).toContain(
    '자동으로 다시 보내지 않았어요',
  );
});
it('clears account input and ignores a late success after account changes', async () => {
  let done!: (value: Awaited<ReturnType<typeof submitSuggestion>>) => void;
  vi.mocked(submitSuggestion).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        done = resolve;
      }),
  );
  await fill('이전 사용자 의견');
  await send();
  await act(async () => root.render(<SuggestionForm key="b" />));
  await act(async () => done({ id: 'one', source: 'MY_PAGE', created_at: '' }));
  expect(host.querySelector('textarea')!.value).toBe('');
  expect(host.querySelector('[role="status"]')).toBeNull();
  expect(vi.mocked(submitSuggestion).mock.calls[0]![1]!.aborted).toBe(true);
});
