import { expect, test } from '@playwright/test';

/** #1143: 드롭다운의 실제 포인터 이벤트와 반응형 너비를 브라우저에서 확인합니다. */
test('피치를 고르면 주소가 바뀌고 목록 안 여백과 바깥 클릭은 구별한다', async ({ page }) => {
  await page.goto('/takes');
  const combo = page.getByRole('combobox', { name: '피치' });
  await expect(combo).toHaveText('전체');

  await combo.click();
  const list = page.getByRole('listbox', { name: '피치' });
  await expect(list).toBeVisible();
  await list.getByRole('option', { name: '투자 설명회' }).click();
  await expect(page).toHaveURL(/pitch=p2/);
  await expect(combo).toHaveText('투자 설명회');

  await combo.click();
  await expect(list).toBeVisible();
  // Playwright locator.click의 position은 padding box 기준입니다. { x: 4, y: 4 }는
  // 첫 번째 옵션을 누를 수 있으므로, 목록의 실제 바깥 경계 기준으로 위쪽 여백을 누릅니다.
  const listBox = await list.boundingBox();
  const firstOptionBox = await list.getByRole('option').first().boundingBox();
  expect(listBox).not.toBeNull();
  expect(firstOptionBox).not.toBeNull();
  const paddingPoint = { x: listBox!.x + 12, y: listBox!.y + 4 };
  // DOM 전역 타입을 사용하지 않는 E2E TypeScript 설정에서도 여백 클릭을 검증합니다.
  expect(paddingPoint.y).toBeLessThan(firstOptionBox!.y);

  await page.mouse.click(paddingPoint.x, paddingPoint.y);
  await expect(list).toBeVisible();
  await expect(page).toHaveURL(/pitch=p2/);

  await page.getByRole('heading', { name: 'Take 기록' }).click();
  await expect(list).toHaveCount(0);
  await expect(page).toHaveURL(/pitch=p2/);
});

test('데스크톱에서는 선택창을 넓게, 320px 화면에서는 목록을 뷰포트 안에 둔다', async ({ page }) => {
  await page.goto('/takes');
  const combo = page.getByRole('combobox', { name: '피치' });
  await expect(combo).toBeVisible();

  const desktop = await combo.boundingBox();
  expect(desktop?.width).toBeGreaterThan(200);

  await page.setViewportSize({ width: 320, height: 568 });
  await combo.click();
  const list = page.getByRole('listbox', { name: '피치' });
  const mobile = await list.boundingBox();
  expect(mobile).not.toBeNull();
  expect(mobile!.x).toBeGreaterThanOrEqual(0);
  expect(mobile!.x + mobile!.width).toBeLessThanOrEqual(320);
});

test('낮은 화면에서도 첫 항목이 보이고 전체 필터로 돌아갈 수 있다', async ({ page }) => {
  for (const viewport of [
    { width: 740, height: 360 },
    { width: 667, height: 320 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/takes?pitch=p2');
    const combo = page.getByRole('combobox', { name: '피치' });
    await combo.click();
    const first = page.getByRole('listbox', { name: '피치' }).getByRole('option', {
      name: '전체',
    });
    await expect(first).toBeVisible();
    const firstBox = await first.boundingBox();
    expect(firstBox).not.toBeNull();
    expect(firstBox!.y).toBeGreaterThanOrEqual(0);
    expect(firstBox!.y + firstBox!.height).toBeLessThanOrEqual(viewport.height);
    await first.click();
    await expect(page).toHaveURL(/\/takes$/);
    await expect(combo).toHaveText('전체');
  }
});
