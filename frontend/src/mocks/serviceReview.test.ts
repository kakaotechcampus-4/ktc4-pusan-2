import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null }));
import { createServiceReviewHandlers } from './serviceReview';
let user: string | null = 'a';
let hasReport = true;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());
beforeEach(() => {
  user = 'a';
  hasReport = true;
  server.resetHandlers(
    ...createServiceReviewHandlers(
      () => user,
      () => hasReport,
    ),
  );
});
const send = (path: string, body: unknown) =>
  fetch('http://localhost/api/' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
const state = () => fetch('http://localhost/api/service-feedback/me').then((r) => r.json());
it('counts distinct approvals, replays the third, then stops after submitting', async () => {
  const ids = [crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID()];
  for (const id of ids)
    expect((await send('service-feedback/impressions', { display_request_id: id })).status).toBe(
      200,
    );
  expect(
    await (await send('service-feedback/impressions', { display_request_id: ids[2] })).json(),
  ).toMatchObject({ should_open: true, display_count: 3 });
  expect(
    await (
      await send('service-feedback/impressions', { display_request_id: crypto.randomUUID() })
    ).json(),
  ).toMatchObject({ should_open: false, reason: 'DISPLAY_LIMIT_REACHED' });
  expect((await send('service-feedback', { rating: 4, suggestion: '  좋은 점  ' })).status).toBe(
    201,
  );
  expect((await send('service-feedback', { rating: 4, suggestion: '좋은 점' })).status).toBe(200);
  expect((await send('service-feedback', { rating: 4, suggestion: '다른 점' })).status).toBe(409);
  expect(
    await (await send('service-feedback/impressions', { display_request_id: ids[2] })).json(),
  ).toMatchObject({ should_open: false, reason: 'ALREADY_SUBMITTED' });
  expect(await state()).toMatchObject({ submitted: true, display_count: 3 });
});
it('accepts repeated suggestions without a report and isolates accounts', async () => {
  hasReport = false;
  const request_id = crypto.randomUUID();
  expect((await send('service-feedback', { rating: 5 })).status).toBe(409);
  expect(
    await (
      await send('service-feedback/impressions', { display_request_id: crypto.randomUUID() })
    ).json(),
  ).toMatchObject({ reason: 'NO_REPORT' });
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
  expect(await state()).toMatchObject({ submitted: false, display_count: 0 });
  user = 'b';
  expect((await send('suggestions', { request_id, content: '다른 계정' })).status).toBe(201);
  user = null;
  expect((await send('suggestions', { request_id, content: '의견' })).status).toBe(401);
});
it.each([0, 6, 2.5, true, '4'])('rejects invalid rating %s', async (rating) =>
  expect((await send('service-feedback', { rating })).status).toBe(422),
);
it('validates content and unknown fields and permits 2000 Unicode characters', async () => {
  for (const content of ['', '  ', '가'.repeat(2001), '\0', '\ud800'])
    expect((await send('suggestions', { request_id: crypto.randomUUID(), content })).status).toBe(
      422,
    );
  expect(
    (await send('suggestions', { request_id: crypto.randomUUID(), content: '🌱'.repeat(2000) }))
      .status,
  ).toBe(201);
  expect((await send('service-feedback', { rating: 4, takeId: 'old' })).status).toBe(422);
  expect((await send('suggestions', { request_id: 'not-uuid', content: '의견' })).status).toBe(422);
});
