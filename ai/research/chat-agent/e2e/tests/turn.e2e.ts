import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

/**
 * 한 턴 풀스택: 쿠키로 로그인 → 시드한 Take 리포트에서 대화 열기 → 질문 → 계획 · 답 문장 → BE 실행 조회 · DB 확인.
 * 상태(`stack.py` 가 쓴 state.json)는 E2E_STATE 로 받는다.
 */
const state = JSON.parse(readFileSync(process.env.E2E_STATE ?? '.stack/state.json', 'utf-8'));
const ROOT = new URL('../../../../../', import.meta.url).pathname;

/** 리프레시 토큰은 쓰면 돌아 바뀌므로 테스트마다 새로 만든다 */
function freshToken(): { refresh_token: string; csrf_token: string } {
  const out = execFileSync(
    'uv',
    ['run', '--project', 'backend', 'python', 'ai/research/chat-agent/e2e/seed.py', 'token'],
    { cwd: ROOT, env: { ...process.env, ...state.be_env }, encoding: 'utf-8' },
  );
  return JSON.parse(out.trim().split('\n').pop()!);
}

function sql(query: string): string {
  return execFileSync(
    'docker',
    ['exec', 'pc-e2e-db-1', 'psql', '-U', 'pc', '-d', 'pc_e2e', '-tAc', query],
    { encoding: 'utf-8' },
  ).trim();
}

test('한 턴: 계획 · 답 문장 · 실행 DONE · DB 행', async ({ page, context }) => {
  const t = freshToken();
  await context.addCookies([
    { name: 'refresh_token', value: t.refresh_token, domain: 'localhost', path: '/api/auth', httpOnly: true, sameSite: 'Lax' },
    { name: 'csrf_token', value: t.csrf_token, domain: 'localhost', path: '/', sameSite: 'Lax' },
  ]);

  await page.goto(`/takes/${state.take_id}`);
  await page.getByRole('button', { name: '채팅 에이전트', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '채팅 에이전트' });
  await expect(panel).toBeVisible();

  const accepted = page.waitForResponse(
    (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/chat/messages',
  );
  // 화면이 부르는 GET /api/chat/runs/{id} 중 status 가 DONE 인 응답 (미리 걸어 둔다)
  const doneRun = page.waitForResponse(async (r) => {
    if (!/^\/api\/chat\/runs\/[0-9a-f-]{36}$/.test(new URL(r.url()).pathname)) return false;
    return (await r.json().catch(() => ({}))).status === 'DONE';
  });
  await panel.getByPlaceholder('메시지를 입력하세요').fill('3장 시선 비율이 어땠어?');
  await panel.getByRole('button', { name: '입력', exact: true }).click();
  const { run_id: runId, conversation_id: conversationId } = await (await accepted).json();

  // (a) 계획 목록과 상태
  const plan = panel.getByRole('list', { name: '할 일' });
  await expect(plan).toBeVisible();
  await expect(plan.locator('[data-plan-status="DONE"]').first()).toBeVisible();
  await expect(plan.locator('[data-plan-status]')).toHaveCount(2);

  // (b) 답 문장
  const claim = panel.locator('[data-message] [data-claim]').filter({ hasText: '28%' });
  await expect(claim).toBeVisible();

  // (c) 위에서 미리 걸어 둔 실행 조회 응답이 DONE
  expect((await doneRun).ok()).toBe(true);

  // (d) DB — 실행 상태 · 메시지 행
  await expect.poll(() => sql(`select status from chat_runs where id='${runId}'`)).toBe('DONE');
  expect(Number(sql(`select count(*) from chat_messages where conversation_id='${conversationId}' and role='user'`))).toBe(1);
  expect(Number(sql(`select count(*) from chat_messages where conversation_id='${conversationId}' and role<>'user'`))).toBe(1);
});
