import { expect, test, type Page } from '@playwright/test';

/**
 * 분석 대기(`/takes/:takeId/processing`) · 대본 확인(`/takes/:takeId/confirm`) E2E.
 * 목(`src/mocks/report.ts`)의 Take id 마다 상태가 정해져 있습니다.
 *   그 밖의 id        처음 물은 뒤로 몇 초마다 단계가 넘어가 정상으로 끝남 (리허설을 끝내고 온 Take)
 *   t-confirm         대본 확인 대기 — 답하거나 건너뛰면 리뷰를 거쳐 리포트로
 *   t-confirm-timeout 보내면 409 `CONFIRMATION_CLOSED`
 *   t-confirm-done    대본 확인을 이미 끝내 리뷰 중
 *   t-failed          분석 실패 · t-held 리뷰 보류
 */

/** 단계가 넘어가 리포트까지 가는 데 몇 초가 걸립니다(목 단계 하나 2.5초) */
const TO_REPORT = { timeout: 30_000 };

/** 확인 항목마다 답을 하나씩 고릅니다 */
async function answerAll(page: Page, label: string) {
  const groups = page.getByRole('group', { name: /번 장에서 어떻게 말했나요/ });
  // 항목이 화면에 뜬 뒤에 셉니다 — `count()` 는 기다리지 않습니다
  await expect(groups.first()).toBeVisible();
  const count = await groups.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i += 1) await groups.nth(i).getByLabel(label).check();
}

test('분석 단계가 차례로 넘어가 끝나면 리포트로 자동 이동한다', async ({ page }) => {
  await page.goto('/takes/t-e2e-new/processing');
  // 진행 표시("1/3 · 발표 기록 정리") — 같은 단계 이름이 화면 읽기용 문구에도 있어 진행 표시로 고릅니다
  await expect(page.getByText(/\/3 · 발표 기록 정리/)).toBeVisible();
  await expect(page).toHaveURL(/\/takes\/t-e2e-new$/, TO_REPORT);
  await expect(page.getByRole('heading', { name: '지난 Take와 비교' })).toBeVisible();
});

test.describe('대본 확인', () => {
  test('분석 대기에서 확인하러 가 답을 보내면 분석을 이어 가 리포트로 간다', async ({ page }) => {
    await page.goto('/takes/t-confirm/processing');
    await page.getByRole('link', { name: '비슷하게 들린 말 확인하기' }).click();
    await expect(page).toHaveURL(/\/takes\/t-confirm\/confirm$/);
    await expect(page.getByRole('heading', { name: '비슷하게 들린 말이 있어요' })).toBeVisible();
    await expect(page.getByRole('timer')).toContainText('남은 시간');

    const send = page.getByRole('button', { name: '답 보내기' });
    // 모든 항목에 답하기 전에는 보낼 수 없습니다
    await expect(send).toBeDisabled();
    await answerAll(page, '대본대로 말했어요');
    await expect(send).toBeEnabled();
    await send.click();

    await expect(page).toHaveURL(/\/takes\/t-confirm$/, TO_REPORT);
  });

  test('건너뛰어도 분석은 이어져 리포트로 간다', async ({ page }) => {
    await page.goto('/takes/t-confirm/confirm');
    await page.getByRole('button', { name: '건너뛰기' }).click();
    await expect(page).toHaveURL(/\/takes\/t-confirm$/, TO_REPORT);
  });

  test('확인 시간이 지난 뒤 보내면(409) 받을 수 없다고 알리고 분석 대기로 돌아갈 길을 준다', async ({
    page,
  }) => {
    await page.goto('/takes/t-confirm-timeout/confirm');
    await answerAll(page, '잘 모르겠어요');
    await page.getByRole('button', { name: '답 보내기' }).click();
    await expect(page.getByText('확인을 받을 수 없어요', { exact: true })).toBeVisible();
    // 답이 반영되지 않았고 분석은 이어진다고 알립니다. 제목과 같은 말을 본문에서 되풀이하지 않습니다
    await expect(
      page.getByText(
        '확인 시간이 지나 이번 답은 반영되지 않았어요. 분석은 답 없이 그대로 이어져요.',
      ),
    ).toBeVisible();
    await expect(page.getByText(/확인을 받을 수 없어요/)).toHaveCount(1);
    await expect(page.getByRole('link', { name: '분석 대기로' })).toBeVisible();
  });

  test('이미 확인이 끝난 Take 는 넘어갔다고 알린다', async ({ page }) => {
    await page.goto('/takes/t-confirm-done/confirm');
    await expect(
      page.getByText('확인 시간이 끝났거나 이미 답했어요. 분석은 그대로 이어져요.'),
    ).toBeVisible();
    await expect(page.getByRole('link', { name: '분석 대기로' })).toBeVisible();
  });
});

test.describe('분석이 끝나지 못한 Take', () => {
  test('분석에 실패하면 분석 대기에서 알리고, 리포트 주소로 와도 분석 대기로 보낸다', async ({
    page,
  }) => {
    await page.goto('/takes/t-failed');
    await expect(page).toHaveURL(/\/takes\/t-failed\/processing$/);
    await expect(page.getByText('이번 Take는 분석하지 못했어요')).toBeVisible();
  });

  test('리뷰가 보류되면 멈췄다고 알리고 리포트로 넘어가지 않는다', async ({ page }) => {
    await page.goto('/takes/t-held/processing');
    await expect(page.getByText('분석이 잠시 멈췄어요')).toBeVisible();
    // 보류는 사람이 풀 때까지 그대로입니다 — 몇 초 기다려도 리포트로 가지 않습니다
    await page.waitForTimeout(3000);
    await expect(page).toHaveURL(/\/takes\/t-held\/processing$/);
  });
});
