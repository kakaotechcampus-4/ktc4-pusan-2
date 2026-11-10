import { appendFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { Api, count, login, openChat, openPanel, seed, send, sql, trackBearer } from './helpers';

/**
 * 실제 모델 시연(Issue #1194) — 로컬 풀스택(진짜 FE · BE · PostgreSQL)에 실제 모델 AI 를 붙여 장면 4개를 새 대화에서 3번씩 돌린다.
 * 장면마다 사용자는 목표만 말하고, 확인 카드의 [승인] 말고는 다음 단계를 지시하지 않는다. 결과는 성공 · 실패 모두
 * `.stack/demo-results.jsonl` 에 남긴다(입력 · 끝 상태 · 걸린 시간 · 할 일 단계 · 답 · 카드 · 승인 횟수). 요금이 든다.
 * 실행: `E2E_REAL_MODEL=1 python3 ai/research/chat-agent/e2e/stack.py run -- npm --prefix ai/research/chat-agent/e2e run demo`
 */
const OUT = new URL('../.stack/demo-results.jsonl', import.meta.url).pathname;
const END = ['DONE', 'PARTIAL', 'REFUSED', 'FAILED', 'CANCELLED', 'EXPIRED'];
const REPS = [1, 2, 3];

async function waitFor(api: Api, runId: string, want: string[]): Promise<string> {
  let status = '';
  await expect
    .poll(async () => (status = (await api.run(runId)).status), { timeout: 150_000, intervals: [1_000] })
    .toMatch(new RegExp(`^(${want.join('|')})$`));
  return status;
}

async function lastAnswer(api: Api, conversationId: string) {
  const messages = (await api.messages(conversationId)).messages;
  const a = [...messages].reverse().find((m: any) => m.role !== 'user') ?? {};
  return {
    status: a.status ?? null,
    text: a.text ?? '',
    plan: (a.plan ?? []).map((p: any) => `${p.status} ${p.step}`),
    cards: (a.cards ?? []).map((c: any) => c.type),
  };
}

function record(row: Record<string, unknown>): void {
  appendFileSync(OUT, JSON.stringify({ at: new Date().toISOString(), ...row }) + '\n');
}

async function approveIfAsked(page: Page, api: Api, runId: string, reload: boolean): Promise<number> {
  const status = await waitFor(api, runId, ['WAITING_APPROVAL', ...END]);
  if (status !== 'WAITING_APPROVAL') return 0;
  let panel = page.getByRole('dialog', { name: '새미나이', exact: true });
  if (reload) {
    await page.reload();
    panel = await openPanel(page);
  }
  await panel.locator('[data-card="ASK_CONFIRM"]').getByRole('button', { name: /^승인/ }).click();
  return 1;
}

for (const rep of REPS) {
  test(`장면 1 · ${rep} — 가장 최근에 연습한 발표를 찾아줘`, async ({ page, context, request }) => {
    const t = seed('latest');
    await login(context, t);
    const api = new Api(request, trackBearer(page));
    await page.goto('/');
    const panel = await openPanel(page);
    const msg = '가장 최근에 연습한 발표를 찾아줘';
    const t0 = Date.now();
    const { runId, conversationId } = await send(page, panel, msg);
    const status = await waitFor(api, runId, END);
    const a = await lastAnswer(api, conversationId);
    const ok =
      status === 'DONE' &&
      a.text.includes("'투자 설명회'") &&
      a.text.includes("'졸업 발표'") &&
      !/가장 최근에 연습한 발표는 '졸업 발표'/.test(a.text);
    record({ scene: 1, rep, msg, run_id: runId, status, ms: Date.now() - t0, approvals: 0, ok, ...a });
    expect(ok, a.text).toBe(true);
  });

  test(`장면 2 · ${rep} — 최근 기록을 보고 다음 목표를 잡아줘`, async ({ page, context, request }) => {
    const t = seed('base'); // 리뷰가 없는 Take 1(코치 결과만)
    await login(context, t);
    const api = new Api(request, trackBearer(page));
    const panel = await openChat(page, t.take_id);
    const msg = '최근 기록을 보고 다음 목표를 잡아줘';
    const t0 = Date.now();
    const { runId, conversationId } = await send(page, panel, msg);
    const approvals = await approveIfAsked(page, api, runId, false);
    const status = await waitFor(api, runId, END);
    const a = await lastAnswer(api, conversationId);
    const goals = count(`select count(*) from chat_goals where conversation_id='${conversationId}'`);
    const ok = status === 'DONE' && approvals === 1 && goals === 1;
    record({ scene: 2, rep, msg, run_id: runId, status, ms: Date.now() - t0, approvals, goals, ok, ...a });
    expect(ok, `${status} approvals=${approvals} goals=${goals} ${a.text}`).toBe(true);
  });

  test(`장면 3 · ${rep} — 두 Take의 시선이 좋아졌는지 비교해줘`, async ({ page, context, request }) => {
    const t = seed('scripts'); // Take 1(대본 판 1) · Take 2(대본 판 2)
    await login(context, t);
    const api = new Api(request, trackBearer(page));
    const panel = await openChat(page, t.take2_id!);
    const msg = '두 Take의 시선이 좋아졌는지 비교해줘';
    const t0 = Date.now();
    const { runId, conversationId } = await send(page, panel, msg);
    const status = await waitFor(api, runId, END);
    const a = await lastAnswer(api, conversationId);
    const said = a.text.replace(/'[^'\n]*'[은는]/g, '');
    const ok =
      ['DONE', 'PARTIAL'].includes(status) &&
      /비교할 수 없|대본 버전/.test(a.text) &&
      !/(좋아졌어요|나빠졌어요|좋아졌습니다|나빠졌습니다)/.test(said);
    record({ scene: 3, rep, msg, run_id: runId, status, ms: Date.now() - t0, approvals: 0, ok, ...a });
    expect(ok, a.text).toBe(true);
  });

  test(`장면 4 · ${rep} — 대본 사용 조건이 달랐어. 다시 확인해줘`, async ({ page, context, request }) => {
    const t = seed('review');
    await login(context, t);
    const api = new Api(request, trackBearer(page));
    const official = () =>
      sql(`select md5(payload::text) || ':' || revision from take_ai_results where take_id='${t.take_id}' and kind='REVIEW'`);
    const before = official();
    const panel = await openChat(page, t.take_id);
    const msg = '대본 보고 한 발표였어. 대본 사용 조건으로 다시 확인해줘';
    const t0 = Date.now();
    const { runId, conversationId } = await send(page, panel, msg);
    const approvals = await approveIfAsked(page, api, runId, true); // 승인 대기 중 새로고침 뒤 승인
    const status = await waitFor(api, runId, END);
    const a = await lastAnswer(api, conversationId);
    const ok = status === 'DONE' && approvals === 1 && a.cards.includes('REASSESS_RESULT') && official() === before;
    record({ scene: 4, rep, msg, run_id: runId, status, ms: Date.now() - t0, approvals, official_same: official() === before, ok, ...a });
    expect(ok, `${status} approvals=${approvals} ${a.cards} ${a.text}`).toBe(true);
  });
}

/**
 * 장면 5 — 실제 화면에서 본 헷갈리는 두 차례 대화의 재현(Issue #1194): 홈(범위 전체)에서 "최근 발표에서 제 목소리 크기가
 * 어땠나요?" → 이어서 "목소리를 작게 말했다고 하는데 어떻게 하면 크게 말할 수 있을까요?". 성공 판정 없이 두 답의 화면
 * 내용(할 일 단계 · 답 글 · 근거 · 카드)을 그대로 남긴다(사람이 읽듯 보는 검토 · 회귀 문항의 재료).
 */
for (const rep of [1, 2]) {
  test(`장면 5 · ${rep} — 최근 발표 목소리 크기 → 크게 말하는 법(두 차례)`, async ({ page, context, request }) => {
    const t = seed('voice');
    await login(context, t);
    const api = new Api(request, trackBearer(page));
    await page.goto('/');
    const panel = await openPanel(page);
    const turns = ['최근 발표에서 제 목소리 크기가 어땠나요?', '목소리를 작게 말했다고 하는데 어떻게 하면 크게 말할 수 있을까요?'];
    const out: unknown[] = [];
    for (const msg of turns) {
      const t0 = Date.now();
      const { runId, conversationId } = await send(page, panel, msg);
      const status = await waitFor(api, runId, END);
      const messages = (await api.messages(conversationId)).messages;
      const a = [...messages].reverse().find((m: any) => m.role !== 'user') ?? {};
      out.push({ msg, run_id: runId, status, ms: Date.now() - t0, plan: a.plan, text: a.text, claims: a.claims, basis: a.basis, cards: a.cards });
      await expect(panel.locator('[data-message]').last()).toBeVisible();
    }
    // 화면에 보이는 할 일 단계 글(FE 표시 그대로)도 남긴다
    const screen = await panel.locator('[data-plan-status]').allInnerTexts();
    record({ scene: 5, rep, turns: out, screen_plan: screen });
  });
}
