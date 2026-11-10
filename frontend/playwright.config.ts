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
  // 파일 안의 시험도 워커들이 나눠 돕니다. 파일 단위로만 나누면 시험이 많은 파일 하나를 워커 하나가 혼자 돌고
  // 나머지는 놀아서, 그 파일의 시간이 곧 전체 시간이 됩니다. 시험마다 브라우저 컨텍스트가 새로 생기고 MSW 목도
  // 페이지 안에서 돌아 시험끼리 상태를 나누지 않습니다 — 새 시험도 앞 시험의 결과에 기대지 않게 씁니다
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    locale: 'ko-KR',
    trace: 'retain-on-failure',
    permissions: ['camera', 'microphone'],
    // 개인정보 처리 동의를 마친 목 사용자(`src/mocks/auth.ts` 의 u1)로 시작합니다.
    // 동의 화면 자체는 `e2e/privacy-consent.e2e.ts` 가 빈 저장소로 따로 봅니다.
    // 키는 `src/shared/lib/privacyConsent.ts` 의 `privacyConsentKey('u1')` 입니다 — 이 설정은 브라우저 코드를 가져올 수 없어
    // 글자로 적습니다. 안내문 버전을 올리면 여기 `v1` 도 올립니다(안 올리면 모든 E2E 가 동의 화면에서 멈춰 바로 드러납니다)
    storageState: {
      cookies: [],
      origins: [
        {
          origin: `http://localhost:${PORT}`,
          localStorage: [
            { name: 'pitchcoach.privacyConsent.v1.u1', value: '2026-11-11T00:00:00.000Z' },
          ],
        },
      ],
    },
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
