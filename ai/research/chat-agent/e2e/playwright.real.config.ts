import { defineConfig, devices } from '@playwright/test';

/**
 * 실제 모델 시연(README '실제 모델 시연') — `E2E_REAL_MODEL=1` 로 띄운 스택에서 장면 4개 × 3번을 돌려 결과를 남긴다.
 * 기본 E2E(`npm test`, 가짜 모델)에는 들지 않는다. 요금이 든다.
 */
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.real.ts',
  timeout: 300_000,
  expect: { timeout: 120_000 },
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
