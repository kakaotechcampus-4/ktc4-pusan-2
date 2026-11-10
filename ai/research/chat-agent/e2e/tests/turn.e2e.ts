import { expect, test } from '@playwright/test';
import { Api, count, login, openChat, script, seed, send, sql, trackBearer, useScenario } from './helpers';

/**
 * 시나리오 1 · 2 — 한 턴과 계획 갱신.
 * 쿠키로 로그인 → 시드한 Take 리포트에서 대화 열기 → 질문 → 화면 · BE API · DB 세 층을 본다.
 * 계획 문구(단계 글자)는 `scripts/*.json` 의 `expect.plan_steps` 에서 읽는다 — 계획 스키마나 문구가 바뀌면 거기와
 * 아래 "계획 문구 확인" 블록만 고친다.
 */
for (const [no, name, title] of [
  [1, 'plan', '한 턴: 계획 · 답 문장 · 실행 DONE · DB 행'],
  [2, 'plan-update', '계획 갱신: 모델이 update_plan 을 부르면 화면 할 일 목록이 새 단계로 바뀐다'],
] as const) {
  test(`시나리오 ${no} — ${title}`, async ({ page, context, request }) => {
    const s = script(name);
    useScenario(name);
    const t = seed('base');
    await login(context, t);
    const api = new Api(request, trackBearer(page));

    const panel = await openChat(page, t.take_id);
    const { runId, conversationId } = await send(page, panel, s.user_message);

    // ── 화면 ──
    const plan = panel.getByRole('list', { name: '할 일' });
    await expect(plan).toBeVisible();
    const claim = panel.locator('[data-message] [data-claim]').filter({ hasText: s.expect.claim });
    await expect(claim).toBeVisible();
    // 계획 문구 확인 (계획 스키마 · 문구가 바뀌면 여기를 고친다 — 단계 글자는 스크립트 JSON 에서 온다)
    const steps: string[] = s.expect.plan_steps;
    await expect(plan.locator('[data-plan-status]')).toHaveCount(steps.length);
    for (const [i, text] of steps.entries()) await expect(plan.locator('[data-plan-status]').nth(i)).toContainText(text);
    // 단계 상태 = 실제 실행: 실행한 단계는 끝(DONE). 끝난 답은 실행하지 않은 단계를 목록에서 빼므로(Issue #1194) 남은 일 · 건너뜀은 없다
    const statuses: string[] = s.expect.plan_statuses;
    for (const [i, st] of statuses.entries()) await expect(plan.locator('[data-plan-status]').nth(i)).toHaveAttribute('data-plan-status', st);

    // ── BE API ──
    await expect.poll(async () => (await api.run(runId)).status).toBe('DONE');
    const messages = (await api.messages(conversationId)).messages;
    expect(messages.map((m: any) => m.role)).toEqual(['user', 'assistant']);
    const answer = messages[1];
    expect(answer.status).toBe('DONE');
    expect(answer.claims.some((c: any) => String(c.text).includes(s.expect.claim))).toBe(true);
    // 계획 문구 확인 (BE 가 저장해 돌려주는 계획)
    expect(answer.plan.map((p: any) => p.step)).toEqual(steps);
    expect(answer.plan.map((p: any) => p.status)).toEqual(statuses);

    // ── DB ──
    expect(sql(`select status from chat_runs where id='${runId}'`)).toBe('DONE');
    expect(count(`select count(*) from chat_messages where conversation_id='${conversationId}' and role='user'`)).toBe(1);
    expect(count(`select count(*) from chat_messages where conversation_id='${conversationId}' and role<>'user'`)).toBe(1);
    // 계획 문구 확인 (DB 에 남은 계획)
    expect(sql(`select string_agg(p->>'step', '|' order by ord) from chat_messages m, jsonb_array_elements(m.plan) with ordinality as e(p, ord) where m.run_id='${runId}'`)).toBe(steps.join('|'));
    expect(sql(`select string_agg(p->>'status', '|' order by ord) from chat_messages m, jsonb_array_elements(m.plan) with ordinality as e(p, ord) where m.run_id='${runId}'`)).toBe(statuses.join('|'));
  });
}
