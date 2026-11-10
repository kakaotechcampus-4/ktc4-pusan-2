import { expect, test } from '@playwright/test';
import { Api, login, openChat, script, seed, send, sql, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 7 — [이어서 확인] 반복 막기(Issue #1194, 기능 문서 7-10): 답 문장이 검사로 모두 빠져 PARTIAL("답 일부를 확인하지
 * 못해 …")로 끝난 뒤 [이어서 확인]을 눌렀는데 같은 까닭으로 또 PARTIAL 이 되면, AI 가 DONE 과 "같은 이유로 더 확인할 수
 * 없어요." 안내로 끝내 버튼이 다시 붙지 않는다(같은 안내가 끝없이 되풀이되는 고리를 끊는다).
 */
test('PARTIAL → 이어서 확인 → 같은 까닭이면 DONE · 같은 이유 안내 · 버튼 없음', async ({ page, context, request }) => {
  const s = script('partial-repeat');
  useScenario('partial-repeat');
  const t = seed('base');
  await login(context, t);
  const api = new Api(request, trackBearer(page));

  const panel = await openChat(page, t.take_id);
  const first = await send(page, panel, s.user_message);

  // 첫 실행: 문장이 모두 검사로 빠져 PARTIAL + [이어서 확인]
  await expect.poll(async () => (await api.run(first.runId)).status).toBe('PARTIAL');
  await expect(panel.locator('[data-message]').last()).toContainText(s.expect.partial_notice);
  const button = panel.getByRole('button', { name: s.expect.continue_button });
  await expect(button).toBeVisible();

  // [이어서 확인] → 같은 까닭이라 DONE 으로 끝내고 같은 이유 안내
  const accepted = page.waitForResponse((r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/chat/messages');
  await button.click();
  const second = await (await accepted).json();
  expect(second.run_id).not.toBe(first.runId);
  await expect.poll(async () => (await api.run(second.run_id)).status).toBe('DONE');
  const last = panel.locator('[data-message]').last();
  await expect(last).toContainText(s.expect.repeat_reason);
  await expect(last).toContainText(s.expect.repeat_split);

  // BE API · DB: 메시지 4개, 실행 PARTIAL → DONE, 두 번째 사용자 말은 이어서 확인 문구 그대로
  const messages = (await api.messages(first.conversationId)).messages;
  expect(messages.map((m: any) => `${m.role}:${m.status ?? ''}`)).toEqual(['user:', 'assistant:PARTIAL', 'user:', 'assistant:DONE']);
  expect(messages[2].text).toBe(s.continue_message);
  expect(sql(`select string_agg(status, ',' order by accepted_at) from chat_runs where conversation_id='${first.conversationId}'`)).toBe('PARTIAL,DONE');
  // 버튼은 PARTIAL 인 마지막 답에만 붙는다 — 다시 붙지 않아 고리가 끊긴다
  await expect(panel.getByRole('button', { name: s.expect.continue_button })).toHaveCount(0);
});
