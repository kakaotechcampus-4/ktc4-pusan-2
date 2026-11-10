import { expect, test } from '@playwright/test';
import { Api, count, login, openChat, seed, sql, sweepNow, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 6 — 리뷰가 끝나면 자동 목표 확인: 열린 대화 목표(Take 1 기준) + 목표 뒤 Take 2(리뷰 DONE)를 시드하고
 * BE 의 자동 목표 확인(goal_sweep.sweep_once)을 지금 한 번 돌리면 TAKE_REVIEWED 실행이 열려(모델 없이) 끝난다.
 * 대화를 다시 열면 GOAL_RESULT 카드가 보이고 DB 목표 상태가 바뀌어 있다.
 * 60초 주기를 기다리지 않으려고 `seed.py sweep` 이 같은 DB · 같은 설정으로 sweep_once() 를 부른다(BE 파일은 읽기만).
 */
test('리뷰 DONE 시드 → 자동 목표 확인 → GOAL_RESULT 카드 · 목표 상태 갱신', async ({ page, context, request }) => {
  useScenario('plan'); // 이 시나리오는 모델을 쓰지 않는다(TAKE_REVIEWED 는 모델 없이 끝난다)
  const t = seed('goal');
  const conversationId = t.conversation_id!;
  expect(sql(`select status from chat_goals where conversation_id='${conversationId}' and version=1`)).toBe('OPEN');

  expect(sweepNow().started).toBeGreaterThanOrEqual(1);

  // DB: TAKE_REVIEWED 실행 DONE 하나, 목표 상태 갱신, 임대 DONE
  const runs = sql(`select string_agg(first_trigger || ':' || status, ',') from chat_runs where conversation_id='${conversationId}'`);
  expect(runs).toBe('TAKE_REVIEWED:DONE');
  expect(sql(`select status from chat_goals where conversation_id='${conversationId}' and version=1`)).toBe('ACHIEVED');
  expect(sql(`select status from chat_leases where conversation_id='${conversationId}' and kind='GOAL_CHECK'`)).toBe('DONE');
  expect(count(`select count(*) from chat_messages where conversation_id='${conversationId}' and role='assistant'`)).toBe(1);

  // 화면: 대화를 다시 열면 GOAL_RESULT 카드
  await login(context, t);
  const api = new Api(request, trackBearer(page));
  const panel = await openChat(page, t.take2_id!);
  // 새 채팅이 열리므로 [대화 목록]에서 시드한 대화를 고른다
  await panel.getByRole('button', { name: '대화 목록' }).click();
  await panel.getByRole('list', { name: '지난 대화' }).getByText('3장 시선 목표').click();
  const card = panel.locator('[data-card="GOAL_RESULT"]');
  await expect(card).toBeVisible();
  await expect(card.locator('[data-goal-result]')).toHaveText('이룸');

  // BE API
  const messages = (await api.messages(conversationId)).messages;
  expect(messages.map((m: any) => m.role)).toEqual(['assistant']);
  expect(messages[0].cards.map((c: any) => c.type)).toEqual(['GOAL_RESULT']);
  expect(messages[0].cards[0].status).toBe('ACHIEVED');
});
