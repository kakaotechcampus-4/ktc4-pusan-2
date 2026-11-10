import { expect, test, type Page } from '@playwright/test';

/**
 * 작업 B: 사이드 패널이 기존 화면의 유효 너비와 조작 영역을 침범하지 않는지 검증합니다.
 * 통합 PR #1171에서 작업 A의 구현 및 기존 chat.e2e.ts와 별도로 화면 회귀를 검증합니다.
 *
 * MSW / 가상 로그인은 playwright.config.ts의 webServer 설정을 사용합니다.
 */
const PITCH_ID = '10000000-0000-4000-8000-000000000001';
const TAKE_ID = '20000000-0000-4000-8000-000000000003';

async function openPanel(page: Page) {
  const launcher = page.getByRole('button', { name: '새미나이 열기', exact: true });
  await expect(launcher).toBeVisible();
  await launcher.click();
  const panel = page.getByRole('dialog', { name: '새미나이' });
  await expect(panel).toBeVisible();
  return panel;
}

test.describe('채팅 사이드 패널 UI 회귀 (작업 B)', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  const screens = [
    { name: '홈', path: '/' },
    { name: '발표 목록', path: '/pitches' },
    { name: '발표 생성', path: '/pitch/new' },
    { name: '발표 수정', path: '/pitch/' + PITCH_ID + '/edit' },
    { name: 'Take 기록', path: '/takes' },
    { name: 'Take 리포트', path: '/takes/' + TAKE_ID },
    { name: '마이페이지', path: '/me' },
  ] as const;

  for (const screen of screens) {
    test(screen.name + ': 헤더·본문과 문서 가로 스크롤 회귀', async ({ page }) => {
      await page.goto(screen.path);
      const content = page.locator('.chat-layout-content');
      const panel = await openPanel(page);
      await expect(content).toHaveCSS('padding-right', '400px');

      const panelBox = await panel.boundingBox();
      const header = content.getByRole('banner').first();
      const main = content.getByRole('main').first();
      await expect(header).toBeVisible();
      await expect(main).toBeVisible();
      const headerBox = await header.boundingBox();
      const mainBox = await main.boundingBox();
      expect(panelBox).not.toBeNull();
      expect(headerBox).not.toBeNull();
      expect(mainBox).not.toBeNull();

      // 우측 패널 왼쪽 경계보다 헤더/본문이 오른쪽으로 더 나가면 겹칩니다.
      expect(headerBox!.x + headerBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
      expect(mainBox!.x + mainBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);

      // 부모 영역뿐 아니라 실제 조작부도 채팅 패널에 가려지지 않아야 합니다.
      // 발표 수정은 지연 로딩 후 고유한 슬라이드 확인 버튼을 기준으로 잡아
      // Suspense 교체 타이밍에 .first()가 다른 요소를 가리키지 않게 합니다.
      const action =
        screen.name === '발표 수정'
          ? main.getByRole('button', { name: /^슬라이드 확인 ·/ })
          : main
              .locator('a:visible, button:visible:not([disabled]):not([aria-disabled="true"])')
              .first();
      await expect(action).toBeVisible();
      await expect.poll(() => action.boundingBox()).not.toBeNull();
      const actionBox = await action.boundingBox();
      expect(actionBox).not.toBeNull();
      expect(actionBox!.x).toBeGreaterThanOrEqual(-1);
      expect(actionBox!.x + actionBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
      await action.click({ trial: true });

      expect(
        await page.evaluate(
          'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1',
        ),
      ).toBe(true);
    });
  }

  test('1366px 오버레이는 헤더 메뉴와 발표 수정 하단 바를 가리지 않는다', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.goto('/pitch/' + PITCH_ID + '/edit');
    const panel = await openPanel(page);
    const header = page.getByRole('banner').first();
    const footer = page.locator('.pitch-create-square > footer');
    await expect(header).toBeVisible();
    await expect(footer).toBeVisible();
    await expect.poll(async () => {
      const [panelBox, headerBox, footerBox] = await Promise.all([
        panel.boundingBox(),
        header.boundingBox(),
        footer.boundingBox(),
      ]);
      return (
        !!panelBox &&
        !!headerBox &&
        !!footerBox &&
        panelBox.y >= headerBox.y + headerBox.height - 1 &&
        panelBox.y + panelBox.height <= footerBox.y + 1
      );
    }).toBe(true);
    await header.getByRole('button', { name: '사용방법' }).click({ trial: true });
    await expect(footer.getByRole('button').last()).toBeInViewport();
  });

  test('1366·1439px는 오버레이이고 1440px부터 도킹된다', async ({ page }) => {
    await page.goto('/');
    const content = page.locator('.chat-layout-content');
    const main = content.getByRole('main');
    const panel = await openPanel(page);

    for (const width of [1366, 1439, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(content).toHaveCSS('padding-right', width >= 1440 ? '400px' : '0px');
      await expect(panel).toHaveCSS('width', '400px');

      const panelBox = await panel.boundingBox();
      const mainBox = await main.boundingBox();
      expect(panelBox).not.toBeNull();
      expect(mainBox).not.toBeNull();
      // 고정 패널의 오른쪽 경계는 스크롤바 너비에 따라 달라질 수 있습니다.
      await expect(panel).toHaveCSS('right', '0px');

      if (width >= 1440) {
        expect(mainBox!.x + mainBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
      } else {
        // 1440px 미만은 의도된 오버레이: 본문 너비를 패널만큼 줄이지 않습니다.
        expect(mainBox!.x + mainBox!.width).toBeGreaterThan(panelBox!.x);
      }
      expect(
        await page.evaluate(
          'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1',
        ),
      ).toBe(true);
    }
  });

  test('발표 생성의 고정 푸터와 주요 버튼이 패널 왼쪽에 남는다', async ({ page }) => {
    await page.goto('/pitch/new');
    const panel = await openPanel(page);
    const footer = page.locator('.pitch-create-square > footer');
    await expect(footer).toHaveCSS('right', '400px');
    const panelBox = await panel.boundingBox();
    const footerBox = await footer.boundingBox();
    expect(footerBox).not.toBeNull();
    expect(panelBox).not.toBeNull();
    expect(footerBox!.x + footerBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
    await expect(footer.getByRole('button').last()).toBeInViewport();
  });

  test('허용 화면 간 이동 후에도 대화 입력 초안과 도킹 상태가 유지된다', async ({ page }) => {
    await page.goto('/');
    const panel = await openPanel(page);
    const input = panel.getByRole('textbox', { name: '채팅 메시지' });
    await input.fill('다른 화면에서도 유지할 입력 초안');
    await page
      .getByRole('navigation', { name: '홈 메뉴' })
      .getByRole('link', { name: 'Take 기록', exact: true })
      .click();
    await expect(page).toHaveURL(/\/takes$/);
    await expect(panel).toBeVisible();
    await expect(input).toHaveValue('다른 화면에서도 유지할 입력 초안');
    await expect(page.locator('.chat-layout-content')).toHaveCSS('padding-right', '400px');
  });

  test('모션 감소에서 패널 및 페이지 애니메이션이 꺼진다', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    const panel = await openPanel(page);
    await expect(panel).toHaveCSS('transition-property', 'none');
    await expect(page.locator('.chat-layout-content')).toHaveCSS('transition-property', 'none');
  });
});

test('375px에서 Take 비교표는 내부 가로 스크롤을 제공한다', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/takes?view=compare&pitch=' + PITCH_ID);
  const region = page.getByRole('region', { name: 'Take별 기록 표 · 좌우로 스크롤' });
  await expect(region).toBeVisible();
  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1',
    ),
  ).toBe(true);
  const overflow = await region.evaluate((element) => element.scrollWidth > element.clientWidth);
  expect(overflow).toBe(true);
  await region.evaluate((element) => {
    element.scrollLeft = element.scrollWidth;
  });
  const scrolled = await region.evaluate((element) => element.scrollLeft);
  expect(scrolled).toBeGreaterThan(0);
});

test('650px 모달은 패널 전후 같은 뷰포트 크기를 유지한다', async ({ page }) => {
  const width = 1440;
  const height = 650;
  await page.setViewportSize({ width, height });
  await page.goto('/pitch/' + PITCH_ID + '/edit');

  const triggers = [
    { name: /^슬라이드 확인 ·/, title: '슬라이드 확인' },
    { name: '매핑 확인/수정', title: '매핑 확인·수정' },
  ] as const;

  const assertBounds = (box: { x: number; y: number; width: number; height: number }) => {
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.y).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    expect(box.y + box.height).toBeLessThanOrEqual(height + 1);
  };

  // 패널을 열기 전에 각 모달의 크기와 위치를 기준값으로 기록합니다.
  const initialBoxes = [];
  for (const trigger of triggers) {
    const button = page.getByRole('button', { name: trigger.name });
    await button.click();
    const modal = page
      .locator('dialog:modal')
      .filter({ has: page.getByRole('heading', { name: trigger.title, exact: true }) });
    await expect(modal).toBeVisible();
    await expect(modal).toHaveCSS('width', '1200px');
    const box = await modal.boundingBox();
    expect(box).not.toBeNull();
    assertBounds(box!);
    initialBoxes.push(box!);
    await page.keyboard.press('Escape');
    await expect(modal).toHaveCount(0);
    await expect(button).toBeFocused();
  }

  const panel = await openPanel(page);
  const input = panel.getByRole('textbox', { name: '채팅 메시지' });
  await input.fill('모달에서 돌아올 입력');

  for (const [index, trigger] of triggers.entries()) {
    const button = page.getByRole('button', { name: trigger.name });
    await button.click();
    const modal = page
      .locator('dialog:modal')
      .filter({ has: page.getByRole('heading', { name: trigger.title, exact: true }) });
    await expect(modal).toBeVisible();
    await expect(modal).toHaveCSS('width', '1200px');
    const box = await modal.boundingBox();
    expect(box).not.toBeNull();
    assertBounds(box!);
    for (const key of ['x', 'y', 'width', 'height'] as const) {
      expect(Math.abs(box![key] - initialBoxes[index][key])).toBeLessThanOrEqual(1);
    }
    await page.keyboard.press('Escape');
    await expect(modal).toHaveCount(0);
    await expect(button).toBeFocused();
    await expect(input).toHaveValue('모달에서 돌아올 입력');
  }
});
