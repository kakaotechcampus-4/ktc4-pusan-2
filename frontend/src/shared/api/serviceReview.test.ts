// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import {
  getServiceReviewState,
  requestServiceReview,
  submitServiceReview,
  submitSuggestion,
} from './serviceReview';
import * as tokens from './tokenStore';
beforeEach(() => {
  vi.spyOn(tokens, 'getAccessToken').mockReturnValue('test-access');
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementation(
        async () => new Response(JSON.stringify({ submitted: true }), { status: 200 }),
      ),
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it('uses the authenticated production endpoints and sends no take/user/source fields', async () => {
  const id = crypto.randomUUID();
  await requestServiceReview(id);
  await submitServiceReview({ rating: 4, suggestion: '의견', category: 'POSITIVE' });
  await submitSuggestion({ request_id: id, content: '추가 의견', category: 'QUESTION' });
  await getServiceReviewState();
  const calls = vi.mocked(fetch).mock.calls;
  expect(calls.map((c) => c[0])).toEqual([
    '/api/service-feedback/impressions',
    '/api/service-feedback',
    '/api/suggestions',
    '/api/service-feedback/me',
  ]);
  expect(calls.slice(0, 3).map((c) => JSON.parse(String(c[1]!.body)))).toEqual([
    { display_request_id: id },
    { rating: 4, suggestion: '의견', category: 'POSITIVE' },
    { request_id: id, content: '추가 의견', category: 'QUESTION' },
  ]);
  for (const call of calls)
    expect(new Headers(call[1]!.headers).get('Authorization')).toBe('Bearer test-access');
});
