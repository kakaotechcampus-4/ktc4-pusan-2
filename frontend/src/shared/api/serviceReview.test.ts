// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { submitSuggestion } from './serviceReview';
import * as tokens from './tokenStore';
beforeEach(() => {
  vi.spyOn(tokens, 'getAccessToken').mockReturnValue('test-access');
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () => new Response(JSON.stringify({}), { status: 201 })),
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it('sends my-page suggestions to the authenticated endpoint without take/user/source fields', async () => {
  const id = crypto.randomUUID();
  await submitSuggestion({ request_id: id, content: '추가 의견', category: 'QUESTION' });
  const calls = vi.mocked(fetch).mock.calls;
  expect(calls.map((c) => c[0])).toEqual(['/api/suggestions']);
  expect(JSON.parse(String(calls[0]![1]!.body))).toEqual({
    request_id: id,
    content: '추가 의견',
    category: 'QUESTION',
  });
  expect(new Headers(calls[0]![1]!.headers).get('Authorization')).toBe('Bearer test-access');
});
