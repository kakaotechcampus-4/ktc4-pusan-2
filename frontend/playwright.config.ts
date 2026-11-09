import { defineConfig, devices } from '@playwright/test';

/**
 * E2E 시험 (`docs/ai/features/chat-agent.md` 12-4 F9) — 목 서버(MSW) 위에서 브라우저로 끝까지 돌립니다.
 *
 * - 시험 파일은 `e2e/*.e2e.ts` 입니다. `*.test.ts` · `*.spec.ts` 로 두면 vitest 가 함께 집어 갑니다.
 * - MSW 는 개발 서버(`import.meta.env.DEV`)에서만 켜져서 `vite build` 결과물이 아니라 `vite` 를 띄웁니다.
 * - 포트는 3100 입니다 — 평소 쓰는 3000 개발 서버(실서버에 붙여 둔 것일 수 있음)를 잘못 재사용하지 않게 늘 새로 띄웁니다.
 * - 로그인은 목의 `VITE_MOCK_SESSION=true` 로 켭니다. `.env.local` 이 있어도 여기 값이 이깁니다.
 */
const PORT = 3100;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    locale: 'ko-KR',
    trace: 'retain-on-failure',
    permissions: ['camera', 'microphone'],
    launchOptions: {
      // 장치 점검 · 리허설 화면용 가짜 카메라 · 마이크 — 권한 창 없이 허용합니다
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
    },
  },
  projects: [
    {
      name: 'chromium',
      // 기기 설정(`Desktop Chrome`)의 viewport 1280×720 이 위 `use` 를 덮어써서, 화면 크기는 기기 설정 뒤에 둡니다
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { VITE_USE_MOCK: 'true', VITE_MOCK_SESSION: 'true' },
  },
});
