import { expect, test } from '@playwright/test';
import { Api, count, login, openChat, script, seed, send, sql, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 5 — 실패 → 중간까지 답함(PARTIAL) → 이어서 확인: 조사 도중 모델이 시간 초과로 실패하면 "여기까지 확인했어요"
 * 머리와 [이어서 확인] 버튼이 보이고, 누르면 새 메시지가 나가 끝까지(DONE) 답한다.
 */
test('실패 → PARTIAL(여기까지 확인했어요 · 이어서 확인) → 이어서 DONE', async ({ page, context, request }) => {
  const s = script('partial-continue');
  useScenario('partial-continue');
  const t = seed('base');
  await login(context, t);
  const api = new Api(request, trackBearer(page));

  const panel = await openChat(page, t.take_id);
  const first = await send(page, panel, s.user_message);

  // 화면: 머리 + 버튼
  await expect(panel.locator('[data-partial]')).toContainText(s.expect.partial_header);
  const button = panel.getByRole('button', { name: s.expect.continue_button });
  await expect(button).toBeVisible();
  // BE API: 첫 실행 PARTIAL, 답 메시지도 PARTIAL
  await expect.poll(async () => (await api.run(first.runId)).status).toBe('PARTIAL');
  let messages = (await api.messages(first.conversationId)).messages;
  expect(messages.map((m: any) => `${m.role}:${m.status ?? ''}`)).toEqual(['user:', 'assistant:PARTIAL']);
  // DB
  expect(sql(`select status from chat_runs where id='${first.runId}'`)).toBe('PARTIAL');

  // [이어서 확인] → 새 메시지가 나가 이어서 끝낸다
  const accepted = page.waitForResponse((r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/chat/messages');
  await button.click();
  const second = await (await accepted).json();
  expect(second.conversation_id).toBe(first.conversationId);
  expect(second.run_id).not.toBe(first.runId);
  await expect(panel.locator('[data-message] [data-claim]').filter({ hasText: s.expect.claim })).toBeVisible();
  await expect.poll(async () => (await api.run(second.run_id)).status).toBe('DONE');

  messages = (await api.messages(first.conversationId)).messages;
  expect(messages.map((m: any) => `${m.role}:${m.status ?? ''}`)).toEqual(['user:', 'assistant:PARTIAL', 'user:', 'assistant:DONE']);
  expect(messages[2].text).toBe(s.continue_message);
  expect(sql(`select string_agg(status, ',' order by accepted_at) from chat_runs where conversation_id='${first.conversationId}'`)).toBe('PARTIAL,DONE');
  expect(count(`select count(*) from chat_messages where conversation_id='${first.conversationId}'`)).toBe(4);
  // 이어서 확인 버튼은 마지막 답에만 — 지금은 끝까지 답했으니 없다
  await expect(panel.getByRole('button', { name: s.expect.continue_button })).toHaveCount(0);
});
