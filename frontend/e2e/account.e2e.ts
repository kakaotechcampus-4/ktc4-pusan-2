import { expect, test } from '@playwright/test';

/**
 * 계정 · 기타 화면 E2E — 마이페이지(`/me`) · 로그아웃 · 없는 주소.
 * 목 로그인(`src/mocks/auth.ts`)은 페이지를 새로 열면 처음 값(로그인됨)으로 돌아가서, 로그아웃 뒤는
 * 새로고침 없이 이어서 봅니다.
 */

test('없는 주소는 길을 잃었다고 알리고 내 피치로 돌려보낸다', async ({ page }) => {
  await page.goto('/no-such-page');
  await expect(page.getByText('어라, 여기는 아무것도 없어. 길을 잃었나 봐!')).toBeVisible();
  await page.getByRole('link', { name: '내 피치로' }).click();
  await expect(page).toHaveURL(/\/$/);
});

test.describe('마이페이지', () => {
  test('계정 정보와 연습 기록 동의를 보이고, 동의를 바꿀 수 있다', async ({ page }) => {
    await page.goto('/me');
    await expect(page.getByRole('heading', { name: '마이페이지' })).toBeVisible();
    await expect(page.getByText('demo@pitchcoach.local')).toBeVisible();

    const consent = page.getByLabel('연습 기록을 남겨 다시 분석하는 데 동의해요');
    const before = await consent.isChecked();
    await consent.click();
    await expect(consent).toBeChecked({ checked: !before });
  });

  test('계정 삭제는 한 번 더 묻고, 확인하면 첫 화면으로 간다', async ({ page }) => {
    await page.goto('/me');
    let asked = '';
    page.once('dialog', (dialog) => {
      asked = dialog.message();
      dialog.accept().catch(() => undefined);
    });
    await page.getByRole('button', { name: '계정 삭제하기' }).click();
    await expect(page).toHaveURL(/\/welcome$/);
    expect(asked).toContain('되돌릴 수 없어요');
  });

  test('계정 삭제 확인을 취소하면 그대로 남는다', async ({ page }) => {
    await page.goto('/me');
    page.once('dialog', (dialog) => {
      dialog.dismiss().catch(() => undefined);
    });
    await page.getByRole('button', { name: '계정 삭제하기' }).click();
    await expect(page).toHaveURL(/\/me$/);
    await expect(page.getByRole('heading', { name: '마이페이지' })).toBeVisible();
  });
});

test('로그아웃하면 첫 화면으로 간다', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '로그아웃' }).click();
  await expect(page).toHaveURL(/\/welcome$/);
});
