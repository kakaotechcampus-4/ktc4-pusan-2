import { expect, test } from '@playwright/test';
import { FIRST_PITCH } from './fixtures.ts';

/**
 * Take 기록(`/takes`) · 지표 비교 E2E — 목(`src/mocks/takeHistory.ts`).
 * 피치 드롭다운의 선택·키보드·반응형 동작은 pitch-select.e2e.ts에서 따로 검증합니다.
 */

const PITCH_ID = FIRST_PITCH.id;

test('기록 목록에서 Take 를 골라 그 리포트로 간다', async ({ page }) => {
  await page.goto('/takes');
  const view = page.getByRole('navigation', { name: 'Take 기록 보기' });
  await expect(view.getByRole('link', { name: '기록 목록' })).toHaveAttribute(
    'aria-current',
    'page',
  );

  const recent = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: '최근 연습' }) });
  const first = recent.getByRole('link').first();
  await expect(first).toBeVisible();
  await first.click();
  await expect(page).toHaveURL(/\/takes\/[^/]+(\/processing)?$/);
});

test('예전 비교 주소는 그 피치의 지표 비교로 이어지고, Take 별 리포트로 갈 수 있다', async ({
  page,
}) => {
  await page.goto(`/pitch/${PITCH_ID}/comparison`);
  await expect(page).toHaveURL(new RegExp(`/takes\\?.*pitch=${PITCH_ID}`));
  await expect(page).toHaveURL(/view=compare/);

  const view = page.getByRole('navigation', { name: 'Take 기록 보기' });
  await expect(view.getByRole('link', { name: '지표 비교' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page.getByRole('heading', { name: '지표 흐름' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Take별 기록' })).toBeVisible();

  await page
    .getByRole('link', { name: /^Take \d+ 리포트 보기$/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/takes\/[^/?]+$/);
});

test('기록 목록 ↔ 지표 비교를 오가도 고른 피치가 유지된다', async ({ page }) => {
  await page.goto(`/takes?pitch=${PITCH_ID}&view=compare`);
  const view = page.getByRole('navigation', { name: 'Take 기록 보기' });
  await view.getByRole('link', { name: '기록 목록' }).click();
  await expect(page).toHaveURL(new RegExp(`pitch=${PITCH_ID}`));
  await expect(view.getByRole('link', { name: '기록 목록' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await view.getByRole('link', { name: '지표 비교' }).click();
  await expect(page).toHaveURL(new RegExp(`pitch=${PITCH_ID}`));
  await expect(page.getByRole('heading', { name: '지표 흐름' })).toBeVisible();
});
