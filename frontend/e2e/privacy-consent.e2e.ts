import { expect, test } from '@playwright/test';

/**
 * 개인정보 처리 동의 E2E — 로그인한 화면은 모두 동의 뒤에 열립니다 (`src/features/auth/session.tsx`).
 * 다른 E2E 는 동의를 마친 사용자로 시작하므로(`playwright.config.ts` 의 storageState) 여기서는 빈 저장소로 시작합니다.
 */
test.use({ storageState: { cookies: [], origins: [] } });

test('동의하기 전에는 홈 위에 동의 창이 뜨고, 동의하면 홈이 열리며 다시 묻지 않는다', async ({
  page,
}) => {
  await page.goto('/');

  const consent = page.getByRole('dialog', { name: '개인정보 처리 및 데이터 활용' });
  await expect(consent).toBeVisible();
  await expect(page.getByRole('button', { name: '새미나이 열기' })).toHaveCount(0);
  // 동의 전에는 Esc 로 닫히지 않습니다
  await page.keyboard.press('Escape');
  await expect(consent).toBeVisible();

  await consent.getByRole('button', { name: '동의하고 시작하기' }).click();

  await expect(consent).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('region', { name: '발표 목록' })).toBeVisible();
  // 동의 전에 '없음'을 본 것이 런처 이름이 바뀌어 지나간 것이 아님을 같은 이름으로 확인합니다
  await expect(page.getByRole('button', { name: '새미나이 열기' })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('region', { name: '발표 목록' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '개인정보 처리 및 데이터 활용' })).toHaveCount(0);
});

test('동의 전에 다른 화면 주소로 바로 들어와도 그 화면 위에 동의 창이 뜨고, 동의하면 그 화면이 열린다', async ({
  page,
}) => {
  await page.goto('/takes');

  await expect(page.getByRole('dialog', { name: '개인정보 처리 및 데이터 활용' })).toBeVisible();

  await page.getByRole('button', { name: '동의하고 시작하기' }).click();

  await expect(page).toHaveURL(/\/takes$/);
  await expect(page.getByRole('heading', { name: 'Take 기록' })).toBeVisible();
});
