import { expect, test, type Page } from '@playwright/test';

/**
 * 장치 점검(`/pitch/:pitchId/device-check`) E2E — 기준 목소리(#1184)를 중심으로 봅니다.
 *
 * 브라우저는 가짜 카메라 · 마이크(`playwright.config.ts` 의 launchOptions)라 권한 창 없이 켜지고,
 * 가짜 마이크의 소리도 말소리로 들립니다. 시선 모델 파일(`public/models`)은 저장소에 없어 시선 기준은
 * "사용 불가"이고 Take 를 시작할 수 없습니다 — 그래서 시작 · 다시 쓰기 · 보정 값 보내기는 여기서 보지 않습니다.
 */

const DEVICE_CHECK = '/pitch/10000000-0000-4000-8000-000000000001/device-check';

/** 기준 목소리를 읽기 시작해 몇 초 뒤 "다 읽었어요" — 너무 빨리 누르면 시간이 모자란 실패입니다 */
async function readBaseVoice(page: Page, seconds: number) {
  await page.getByRole('button', { name: /^(목소리 재기|다시 읽기)$/ }).click();
  await page.waitForTimeout(seconds * 1000);
  await page.getByRole('button', { name: '다 읽었어요' }).click();
}

test.beforeEach(async ({ page }) => {
  await page.goto(DEVICE_CHECK);
  await expect(page.getByRole('heading', { name: '시작 전 세팅' })).toBeVisible();
});

test('카메라 · 마이크가 켜지고, 기준 목소리는 재기 전이라 시작할 수 없다', async ({ page }) => {
  await expect(page.getByText('마이크 입력 확인됨')).toBeVisible();
  await expect(page.getByRole('button', { name: '목소리 재기' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Take \d+ 시작하기$/ })).toBeDisabled();
});

test('기준 목소리를 끝까지 읽으면 확인됨이 되고 들은 시간을 알린다', async ({ page }) => {
  await readBaseVoice(page, 8);
  await expect(page.getByText('확인됨', { exact: true })).toBeVisible();
  await expect(page.getByText(/기준 목소리를 [\d.]+초 들었어요/)).toBeVisible();
  // 다시 잴 수 있습니다
  await expect(page.getByRole('button', { name: '다시 읽기' })).toBeVisible();
});

test('읽는 중에 그만두면 다시 잴 수 있는 처음 상태로 돌아간다', async ({ page }) => {
  await page.getByRole('button', { name: '목소리 재기' }).click();
  await expect(page.getByRole('button', { name: '다 읽었어요' })).toBeVisible();
  await page.getByRole('button', { name: '그만두기' }).click();
  await expect(page.getByRole('button', { name: '목소리 재기' })).toBeVisible();
  await expect(page.getByRole('button', { name: '다 읽었어요' })).toHaveCount(0);
});

test('두 번 연달아 읽기에 실패하면 발표 처음 목소리로 시작하는 탈출구가 생긴다 (#1184)', async ({
  page,
}) => {
  const escape = page.getByRole('button', { name: '발표 처음 목소리를 기준으로 시작하기' });

  await readBaseVoice(page, 0.5);
  // 너무 짧게 읽으면 실패입니다 — 이유는 "조금 더 읽어 주세요" · "잘 들리지 않았어요" 중 하나라 문구 대신 상태로 봅니다
  await expect(page.getByRole('button', { name: '다시 읽기' })).toBeVisible();
  await expect(page.getByText('확인됨', { exact: true })).toHaveCount(0);
  // 한 번 실패로는 탈출구를 보이지 않습니다
  await expect(escape).toHaveCount(0);

  await readBaseVoice(page, 0.5);
  await expect(escape).toBeVisible();
  await escape.click();
  await expect(page.getByText('발표 처음 목소리로')).toBeVisible();
});
