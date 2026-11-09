import { expect, test } from '@playwright/test';
import { Api, count, login, openChat, script, seed, send, sql, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 3 — 승인 대기 → 새로고침 → 승인 → 끝: 목표 저장(SAVE_GOAL, ASK) 확인 카드가 새로고침 뒤에도 남고,
 * [승인] 하면 실행이 이어져 DONE 이 되며 목표 행이 정확히 한 번만 저장된다.
 */
test('승인 대기 → 새로고침 → 승인 → 목표 저장(한 번만)', async ({ page, context, request }) => {
  const s = script('approve-goal');
  useScenario('approve-goal');
  const t = seed('base');
  await login(context, t);
  const bearer = trackBearer(page);
  const api = new Api(request, bearer);

  let panel = await openChat(page, t.take_id);
  const { runId, conversationId } = await send(page, panel, s.user_message);

  // 화면: 확인 카드(승인 · 거절)
  let card = panel.locator('[data-card="ASK_CONFIRM"]');
  await expect(card).toBeVisible();
  await expect(card.getByRole('button', { name: /^승인/ })).toBeVisible();
  await expect(card.getByRole('button', { name: /^거절/ })).toBeVisible();
  // BE: 실행 상태 WAITING_APPROVAL + 대기 카드
  await expect.poll(async () => (await api.run(runId)).status).toBe('WAITING_APPROVAL');
  expect((await api.run(runId)).cards.map((c: any) => c.type)).toEqual(['ASK_CONFIRM']);
  // DB: 행동 PENDING, 목표 행 아직 없음
  expect(sql(`select status from chat_actions where run_id='${runId}'`)).toBe('PENDING');
  expect(count(`select count(*) from chat_goals where conversation_id='${conversationId}' and goal is not null`)).toBe(0);

  // 새로고침 — 카드가 그대로 남는다(상태가 화면이 아니라 BE 에 있다)
  await page.reload();
  await page.getByRole('button', { name: '채팅 에이전트', exact: true }).click();
  panel = page.getByRole('dialog', { name: '채팅 에이전트' });
  card = panel.locator('[data-card="ASK_CONFIRM"]');
  await expect(card).toBeVisible();
  expect((await api.run(runId)).status).toBe('WAITING_APPROVAL');

  // 승인 → 이어서 끝난다
  await card.getByRole('button', { name: /^승인/ }).click();
  await expect(panel.locator('[data-message] [data-claim]').filter({ hasText: s.expect.result_text })).toBeVisible();
  await expect(panel.locator('[data-card="GOAL_SAVED"]')).toBeVisible();
  await expect(panel.locator('[data-card="ASK_CONFIRM"]')).toHaveCount(0);
  await expect.poll(async () => (await api.run(runId)).status).toBe('DONE');

  // DB: 행동 SUCCEEDED 한 번, 목표 행 정확히 하나, 답 메시지 하나(중복 실행 없음)
  expect(sql(`select status from chat_actions where run_id='${runId}'`)).toBe('SUCCEEDED');
  expect(count(`select count(*) from chat_actions where run_id='${runId}'`)).toBe(1);
  expect(count(`select count(*) from chat_goals where conversation_id='${conversationId}' and goal is not null and status='OPEN'`)).toBe(1);
  expect(sql(`select goal_version from chat_conversations where id='${conversationId}'`)).toBe('1');
  expect(count(`select count(*) from chat_messages where conversation_id='${conversationId}' and role<>'user'`)).toBe(1);
  const messages = (await api.messages(conversationId)).messages;
  expect(messages.at(-1).text).toContain(s.expect.result_text);
  expect(messages.at(-1).cards.map((c: any) => c.type)).toEqual(['GOAL_SAVED']);
});
