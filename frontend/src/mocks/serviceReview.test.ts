import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null }));
import { createServiceReviewHandlers } from './serviceReview';
let user: string | null = 'a';
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());
beforeEach(() => {
  user = 'a';
  server.resetHandlers(...createServiceReviewHandlers(() => user));
});
const send = (path: string, body: unknown) =>
  fetch('http://localhost/api/' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
it('accepts repeated suggestions and isolates accounts', async () => {
  const request_id = crypto.randomUUID();
  const first = await send('suggestions', { request_id, content: '의견' });
  expect(first.status).toBe(201);
  const receipt = await first.json();
  expect(await (await send('suggestions', { request_id, content: ' 의견 ' })).json()).toEqual(
    receipt,
  );
  expect((await send('suggestions', { request_id, content: '수정' })).status).toBe(409);
  expect(
    (await send('suggestions', { request_id: crypto.randomUUID(), content: '의견' })).status,
  ).toBe(201);
  user = 'b';
  expect((await send('suggestions', { request_id, content: '다른 계정' })).status).toBe(201);
  user = null;
  expect((await send('suggestions', { request_id, content: '의견' })).status).toBe(401);
});
it('validates content and unknown fields and permits 2000 Unicode characters', async () => {
  for (const content of ['', '  ', '가'.repeat(2001), '\0', '\ud800'])
    expect((await send('suggestions', { request_id: crypto.randomUUID(), content })).status).toBe(
      422,
    );
  expect(
    (await send('suggestions', { request_id: crypto.randomUUID(), content: '🌱'.repeat(2000) }))
      .status,
  ).toBe(201);
  expect((await send('suggestions', { request_id: 'not-uuid', content: '의견' })).status).toBe(422);
});
it.each(['POSITIVE', 'IMPROVEMENT', 'PROBLEM', 'QUESTION', 'OTHER'])(
  'stores %s and rejects a changed category on retry',
  async (category) => {
    const changed = category === 'OTHER' ? 'QUESTION' : 'OTHER';
    const suggestion = { request_id: crypto.randomUUID(), content: '의견', category };
    const saved = await send('suggestions', suggestion);
    expect(saved.status).toBe(201);
    const receipt = await saved.json();
    expect(receipt).toMatchObject({ category, source: 'MY_PAGE' });
    expect(await (await send('suggestions', suggestion)).json()).toEqual(receipt);
    const conflict = await send('suggestions', { ...suggestion, category: changed });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toMatchObject({ code: 'SUGGESTION_REQUEST_CONFLICT' });
  },
);
it.each([null, 'UNKNOWN', 'positive', '', 1, {}, 'toString'])(
  'rejects invalid category %j',
  async (category) => {
    expect(
      (await send('suggestions', { request_id: crypto.randomUUID(), content: '의견', category }))
        .status,
    ).toBe(422);
  },
);
it('defaults omitted categories to OTHER', async () => {
  const suggestion = { request_id: crypto.randomUUID(), content: '의견' };
  expect(await (await send('suggestions', suggestion)).json()).toMatchObject({ category: 'OTHER' });
  expect((await send('suggestions', { ...suggestion, category: 'OTHER' })).status).toBe(200);
});
