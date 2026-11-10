import { defineConfig, devices } from '@playwright/test';

/**
 * 대화 에이전트 풀스택 E2E — 진짜 FE · BE · PostgreSQL · AI 앱(가짜 모델)을 `stack.py` 가 띄운 상태에서 돈다.
 * 직접 부르지 않고 `python3 ai/research/chat-agent/e2e/stack.py run -- npm --prefix ai/research/chat-agent/e2e test`.
 * 서버는 여기서 띄우지 않는다(webServer 없음).
 */
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.e2e.ts',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3100',
    locale: 'ko-KR',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
});
