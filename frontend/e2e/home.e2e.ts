import { expect, test } from '@playwright/test';
import { DELETABLE_PITCH_TITLE, FIRST_PITCH } from './fixtures.ts';

/**
 * 홈(`/`) E2E — 목(`src/mocks/home.ts`)의 피치 넷 가운데
 *   '캡스톤 최종 발표'   분석이 도는 중이라 BE 가 삭제를 막음(`can_delete: false`)
 *   '창업 경진대회 본선' 지울 수 있음
 */

const BUSY = FIRST_PITCH.title;
const DELETABLE = DELETABLE_PITCH_TITLE;

test('발표 목록에 피치가 보인다', async ({ page }) => {
  await page.goto('/');
  const list = page.getByRole('region', { name: '발표 목록' });
  await expect(list.getByText(BUSY)).toBeVisible();
  await expect(list.getByText(DELETABLE)).toBeVisible();
});

test.describe('피치 삭제', () => {
  test('더보기 → 삭제 → 확인하면 목록에서 사라지고 지웠다고 알린다', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: `${DELETABLE} 더보기` }).click();
    await page.getByRole('menuitem', { name: '삭제' }).click();

    const dialog = page.getByRole('dialog', { name: '이 피치를 삭제할까요?' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(DELETABLE);
    // Take 가 있는 피치라 Take 기록과 리포트도 함께 지워진다고 알립니다
    await expect(dialog).toContainText(/Take 기록 \d+개와 리포트/);
    await dialog.getByRole('button', { name: '삭제', exact: true }).click();

    await expect(page.getByText(`‘${DELETABLE}’ 피치를 삭제했어요.`)).toBeVisible();
    await expect(page.getByRole('region', { name: '발표 목록' }).getByText(DELETABLE)).toHaveCount(
      0,
    );
  });

  test('분석이 도는 피치는 삭제 메뉴가 막혀 있고 이유를 보인다', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: `${BUSY} 더보기` }).click();
    const item = page.getByRole('menuitem', { name: '삭제' });
    await expect(item).toHaveAttribute('aria-disabled', 'true');
    // 막힌 이유를 메뉴 설명으로 읽어 줍니다
    await expect(item).toHaveAccessibleDescription(/.+/);
    // 눌러도 삭제 창이 열리지 않습니다 (aria-disabled 라 Playwright 는 기다리므로 강제로 누릅니다)
    await item.click({ force: true });
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });
});

test('사용방법 창을 열고 Esc 로 닫으면 연 버튼으로 초점이 돌아간다', async ({ page }) => {
  await page.goto('/');
  const opener = page.getByRole('button', { name: '사용방법 살펴보기' });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '사용방법' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test.describe('바닥글', () => {
  test('TIP 자동 전환을 멈추고 다시 켤 수 있다', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '팁 자동 전환 일시정지' }).click();
    await expect(page.getByRole('button', { name: '팁 자동 전환 재개' })).toBeVisible();
    await page.getByRole('button', { name: '팁 자동 전환 재개' }).click();
    await expect(page.getByRole('button', { name: '팁 자동 전환 일시정지' })).toBeVisible();
  });

  test('이용약관 · 개인정보 처리방침으로 가고, 홈으로 돌아올 수 있다', async ({ page }) => {
    await page.goto('/');
    const policy = page.getByRole('navigation', { name: '서비스 정책' });
    await policy.getByRole('link', { name: '이용약관' }).click();
    await expect(page).toHaveURL(/\/terms\?from=home$/);
    await page.getByRole('link', { name: '홈으로 돌아가기' }).click();
    await expect(page).toHaveURL(/\/$/);

    await page
      .getByRole('navigation', { name: '서비스 정책' })
      .getByRole('link', { name: '개인정보 처리방침' })
      .click();
    await expect(page).toHaveURL(/\/privacy\?from=home$/);
  });
});
