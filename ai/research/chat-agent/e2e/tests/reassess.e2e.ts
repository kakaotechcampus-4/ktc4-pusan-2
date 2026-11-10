import { expect, test } from '@playwright/test';
import { Api, count, login, openChat, script, seed, send, sql, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 4 — 다시 평가(REASSESS) 재개: 사용자가 리뷰에 이의를 제기하면 확인 카드 → 승인 → BE 가 AI `/takes/review` 를
 * (모델 없이) 불러 → 재개 → 답에 "참고 결과 — 공식 리뷰는 그대로예요" 카드. 공식 리뷰 행은 그대로다.
 */
test('다시 평가: 승인 → /takes/review(모델 없음) → 참고 결과 카드, 공식 리뷰 그대로', async ({ page, context, request }) => {
  const s = script('reassess');
  useScenario('reassess');
  const t = seed('review');
  await login(context, t);
  const api = new Api(request, trackBearer(page));
  const officialBefore = sql(`select md5(payload::text) || ':' || revision from take_ai_results where take_id='${t.take_id}' and kind='REVIEW'`);
  expect(officialBefore).not.toBe('');

  const panel = await openChat(page, t.take_id);
  const { runId, conversationId } = await send(page, panel, s.user_message);

  // 화면: 다시 평가 확인 카드
  const confirm = panel.locator('[data-card="ASK_CONFIRM"]');
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText('참고 결과이고 공식 결과는 그대로예요');
  await expect.poll(async () => (await api.run(runId)).status).toBe('WAITING_APPROVAL');
  expect(sql(`select type || ':' || status from chat_actions where run_id='${runId}'`)).toBe('REASSESS:PENDING');

  await confirm.getByRole('button', { name: /^승인/ }).click();

  // 화면: 참고 결과 카드
  const result = panel.locator('[data-card="REASSESS_RESULT"]');
  await expect(result).toBeVisible();
  await expect(result).toContainText(s.expect.card_title);
  await expect(panel.locator('[data-card="ASK_CONFIRM"]')).toHaveCount(0);

  // BE API
  await expect.poll(async () => (await api.run(runId)).status).toBe('DONE');
  const messages = (await api.messages(conversationId)).messages;
  expect(messages.at(-1).cards.map((c: any) => c.type)).toEqual(['REASSESS_RESULT']);

  // DB: 행동 성공 1건(결과에 전·후 리뷰 응답 + 모델이 없어 기본 결과) · 공식 리뷰는 한 글자도 안 바뀜
  expect(sql(`select type || ':' || status from chat_actions where run_id='${runId}'`)).toBe('REASSESS:SUCCEEDED');
  expect(sql(`select result->'data'->'after'->>'fallback_reason' from chat_actions where run_id='${runId}'`)).toBe('LLM_UNAVAILABLE');
  expect(sql(`select md5(payload::text) || ':' || revision from take_ai_results where take_id='${t.take_id}' and kind='REVIEW'`)).toBe(officialBefore);
  expect(count(`select count(*) from take_ai_results where take_id='${t.take_id}' and kind='REVIEW'`)).toBe(1);
  expect(count(`select count(*) from chat_messages where conversation_id='${conversationId}' and role<>'user'`)).toBe(1);
});
