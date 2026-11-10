import { expect, test } from '@playwright/test';

/**
 * #1105 First Take 튜토리얼: 홈 도움말 진입, 샘플 생성, 사용 완료 분기를
 * 실제 라우터와 개발 목(MSW)으로 검증합니다.
 *
 * 시선 모델·실서버 분석은 이 환경에서 사용할 수 없어 리허설 완료와
 * 리포트 생성까지는 단위 테스트 및 실서버 E2E에서 별도로 확인합니다.
 */
test('홈 사용방법에서 First Take 튜토리얼로 이동한다 (#944 연동)', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '사용방법 살펴보기' }).click();

  const dialog = page.getByRole('dialog', { name: '사용방법' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('link', { name: /튜토리얼 해 보기/ }).click();

  await expect(page).toHaveURL(/\/pitch\/tutorial$/);
  await expect(page.getByRole('heading', { name: '치치와 함께하는 첫 번째 Take' })).toBeVisible();
});

test('샘플을 만들면 해당 피치의 장치 점검으로 이동한다', async ({ page }) => {
  await page.goto('/pitch/tutorial');
  await page.getByRole('button', { name: '튜토리얼 시작하기 →' }).click();

  await expect(page).toHaveURL(/\/pitch\/[^/]+\/device-check$/);
  await expect(page.getByRole('heading', { name: '시작 전 세팅' })).toBeVisible();
});

test('튜토리얼 사용 완료 계정은 장치 점검으로 보내지 않고 새 피치를 안내한다', async ({ page }) => {
  await page.goto('/pitch/tutorial');
  const start = page.getByRole('button', { name: '튜토리얼 시작하기 →' });
  await expect(start).toBeVisible();

  // 개발용 MSW 모듈은 화면과 같은 브라우저 안에 있으므로 새로고침 없이 상태를 바꿉니다.
  await page.evaluate(() => {
    const mock = Reflect.get(globalThis, 'mockFirstTake') as { used(): void } | undefined;
    if (!mock) throw new Error('First Take 개발 목이 등록되지 않았습니다.');
    mock.used();
  });

  await start.click();
  await expect(page).toHaveURL(/\/pitch\/tutorial$/);
  await expect(page.getByRole('heading', { name: '튜토리얼 리포트를 이미 받았어요' })).toBeVisible();
  await expect(page.getByRole('link', { name: '새 피치 만들기' })).toHaveAttribute('href', '/pitch/new');
  await expect(page.getByRole('link', { name: '샘플 Take 기록 보기' })).toHaveCount(0);
});
