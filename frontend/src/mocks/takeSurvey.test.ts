import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null, mockTestConsentAt: () => null }));
import { createTakeSurveyHandlers } from './takeSurvey';
import { homeFixture } from './home';

let user: string | null = 'a';
let consentedAt: string | null = null;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());
beforeEach(() => {
  user = 'a';
  consentedAt = null;
  server.resetHandlers(
    ...createTakeSurveyHandlers(
      () => user,
      () => consentedAt,
    ),
  );
});

const takes = homeFixture.pitches.flatMap((p) => p.takes);
const completed = takes.find((t) => t.status === 'COMPLETED')!.take_id;
const unfinished = takes.find((t) => t.status !== 'COMPLETED')!.take_id;
const answer = { accuracy_rating: 4, usefulness_rating: 3, comment: '아쉬움', question_version: 1 };
const post = (takeId: string, body: unknown) =>
  fetch(`http://localhost/api/takes/${takeId}/survey`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
const pending = () =>
  fetch('http://localhost/api/take-surveys/pending').then(
    (r) => r.json() as Promise<{ items: { take_id: string }[] }>,
  );

it('처음은 201, 같은 답은 200, 다른 답은 409 로 BE 계약과 같다', async () => {
  expect((await post(completed, answer)).status).toBe(201);
  expect((await post(completed, { ...answer, comment: '  아쉬움  ' })).status).toBe(200);
  const conflict = await post(completed, { ...answer, accuracy_rating: 5 });
  expect(conflict.status).toBe(409);
  expect(await conflict.json()).toMatchObject({ code: 'TAKE_SURVEY_ALREADY_SUBMITTED' });
  const state = await (await fetch(`http://localhost/api/takes/${completed}/survey`)).json();
  expect(state).toMatchObject({ submitted: true, survey: { accuracy_rating: 4 } });
});

it('리포트가 없는 Take 는 409, 틀린 값은 422 로 거절한다', async () => {
  const blocked = await post(unfinished, answer);
  expect(blocked.status).toBe(409);
  expect(await blocked.json()).toMatchObject({ code: 'TAKE_SURVEY_NOT_ELIGIBLE' });
  for (const body of [
    { ...answer, accuracy_rating: 0 },
    { ...answer, usefulness_rating: 6 },
    { ...answer, accuracy_rating: '4' },
    { ...answer, comment: '가'.repeat(2001) },
    { ...answer, take_id: completed },
  ])
    expect((await post(completed, body)).status).toBe(422);
});

it('미작성 목록은 동의한 뒤에만 채우고, 낸 설문은 뺀다', async () => {
  expect((await pending()).items).toEqual([]);
  consentedAt = '2026-10-30T00:00:00Z';
  const before = (await pending()).items.map((i) => i.take_id);
  expect(before).toContain(completed);
  expect(before).not.toContain(unfinished);
  await post(completed, answer);
  expect((await pending()).items.map((i) => i.take_id)).not.toContain(completed);
  user = 'b';
  expect((await pending()).items.map((i) => i.take_id)).toContain(completed);
});
