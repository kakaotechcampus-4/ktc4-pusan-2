import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { expect, type APIRequestContext, type BrowserContext, type Locator, type Page } from '@playwright/test';

/**
 * 시나리오 테스트가 함께 쓰는 도우미 — 시드 · 시나리오 고르기 · 로그인 · 대화 열기/보내기 · BE API · DB 조회.
 * 상태(`stack.py` 가 쓴 state.json)는 E2E_STATE 로 받는다.
 */
export const state = JSON.parse(readFileSync(process.env.E2E_STATE ?? '.stack/state.json', 'utf-8'));
export const ROOT = new URL('../../../../../', import.meta.url).pathname;
const E2E = new URL('../', import.meta.url).pathname;

export type Seeded = {
  user_id: string;
  pitch_id: string;
  take_id: string;
  refresh_token: string;
  csrf_token: string;
  conversation_id?: string;
  take2_id?: string;
};

/** 가짜 모델 대본 파일(`scripts/<이름>.json`). 말 · 기대 문구는 여기서 읽는다 */
export type Script = {
  user_message: string;
  expect: Record<string, any>;
};
export function script(name: string): Script {
  return JSON.parse(readFileSync(`${E2E}scripts/${name}.json`, 'utf-8'));
}

/** 가짜 모델이 쓸 대본을 고른다 — 모델을 부를 때마다 이 파일을 본다(파일이 바뀌면 줄을 새로 채움) */
export function useScenario(name: string): void {
  writeFileSync(state.scenario_file, name);
}

/** 테스트 하나가 쓸 새 사용자 · 피치 · Take (종류: base | review | goal) */
export function seed(kind: 'base' | 'review' | 'goal'): Seeded {
  const out = execFileSync(
    'uv',
    ['run', '--project', 'backend', 'python', 'ai/research/chat-agent/e2e/seed.py', 'new', kind],
    { cwd: ROOT, env: { ...process.env, ...state.be_env }, encoding: 'utf-8' },
  );
  return JSON.parse(out.trim().split('\n').pop()!);
}

/** BE 앱 안의 자동 목표 확인(sweep_once)을 지금 한 번 돌리고 띄운 실행이 끝나기를 기다린다 */
export function sweepNow(): { started: number } {
  const out = execFileSync(
    'uv',
    ['run', '--project', 'backend', 'python', 'ai/research/chat-agent/e2e/seed.py', 'sweep'],
    { cwd: ROOT, env: { ...process.env, ...state.be_env }, encoding: 'utf-8' },
  );
  return JSON.parse(out.trim().split('\n').pop()!);
}

export function sql(query: string): string {
  return execFileSync('docker', ['exec', 'pc-e2e-db-1', 'psql', '-U', 'pc', '-d', 'pc_e2e', '-tAc', query], {
    encoding: 'utf-8',
  }).trim();
}
export const count = (query: string): number => Number(sql(query));

export async function login(context: BrowserContext, t: Seeded): Promise<void> {
  await context.addCookies([
    { name: 'refresh_token', value: t.refresh_token, domain: 'localhost', path: '/api/auth', httpOnly: true, sameSite: 'Lax' },
    { name: 'csrf_token', value: t.csrf_token, domain: 'localhost', path: '/', sameSite: 'Lax' },
  ]);
}

/** 리포트 화면에서 채팅 패널을 연다 */
export async function openChat(page: Page, takeId: string): Promise<Locator> {
  await page.goto(`/takes/${takeId}`);
  await page.getByRole('button', { name: '채팅 에이전트', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '채팅 에이전트' });
  await expect(panel).toBeVisible();
  return panel;
}

/** 말을 보내고 BE 가 돌려준 ids 를 받는다 */
export async function send(page: Page, panel: Locator, text: string) {
  const accepted = page.waitForResponse(
    (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/chat/messages',
  );
  await panel.getByPlaceholder('메시지를 입력하세요').fill(text);
  await panel.getByRole('button', { name: '입력', exact: true }).click();
  const body = await (await accepted).json();
  return { runId: body.run_id as string, conversationId: body.conversation_id as string, messageId: body.message_id as string };
}

/** 화면이 BE 를 부를 때 쓴 Bearer 토큰 — 테스트가 같은 사용자로 BE API 를 직접 읽는 데 쓴다 */
export function trackBearer(page: Page): () => string {
  let bearer = '';
  page.on('request', (r) => {
    const h = r.headers()['authorization'];
    if (h && new URL(r.url()).pathname.startsWith('/api/')) bearer = h;
  });
  return () => bearer;
}

export class Api {
  constructor(private request: APIRequestContext, private bearer: () => string) {}
  private async get(path: string) {
    const r = await this.request.get(`${state.be}${path}`, { headers: { authorization: this.bearer() } });
    expect(r.ok(), `${path} -> ${r.status()}`).toBe(true);
    return r.json();
  }
  run(runId: string) {
    return this.get(`/api/chat/runs/${runId}`);
  }
  messages(conversationId: string) {
    return this.get(`/api/chat/conversations/${conversationId}/messages`);
  }
  latestRun(conversationId: string) {
    return this.get(`/api/chat/conversations/${conversationId}/run`);
  }
}
