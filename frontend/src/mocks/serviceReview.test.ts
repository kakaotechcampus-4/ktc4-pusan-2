import { setupServer } from 'msw/node';
import { afterAll, beforeAll, expect, it } from 'vitest';
import { serviceReviewHandlers } from './serviceReview';
const server = setupServer(...serviceReviewHandlers);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());
const send = (id: string, rating: number) =>
  fetch(`http://localhost/api/mock/service-reviews/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating }),
  });
it.each([0, 6, 2.5])('rejects rating %s', async (rating) => {
  expect((await send('invalid', rating)).status).toBe(422);
});
it('returns the original receipt on duplicate submissions', async () => {
  await send('duplicate', 2);
  expect(await (await send('duplicate', 5)).json()).toEqual({ take_id: 'duplicate', rating: 2 });
});
it('fails once and accepts the same rating on retry', async () => {
  expect((await send('t-review-retry', 1)).status).toBe(503);
  expect((await send('t-review-retry', 1)).status).toBe(200);
});
