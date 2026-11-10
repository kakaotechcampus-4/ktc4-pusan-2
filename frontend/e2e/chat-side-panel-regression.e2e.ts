import { expect, test, type Page } from '@playwright/test';

/**
 * 작업 B: 사이드 패널이 기존 화면의 유효 너비와 조작 영역을 침범하지 않는지 검증합니다.
 * 통합 PR #1171에서 작업 A의 구현 및 기존 chat.e2e.ts와 별도로 화면 회귀를 검증합니다.
 *
 * MSW / 가상 로그인은 playwright.config.ts의 webServer 설정을 사용합니다.
 */
const PITCH_ID = '10000000-0000-4000-8000-000000000001';
const TAKE_ID = '20000000-0000-4000-8000-000000000003';

type Rect = { x: number; y: number; width: number; height: number };

function overlaps(a: Rect, b: Rect) {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  );
}

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

  test('우측 런처: 화면 너비별 숨김과 초점 복귀', async ({ page }) => {
    for (const width of [375, 761, 1366, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/');
      const container = page.locator('[data-chat-launcher]');
      const launcher = container.locator(':scope > button');
      await expect(container).toHaveCSS('right', '8px');
      await expect(launcher).toBeVisible();

      await launcher.click();
      const panel = page.getByRole('dialog', { name: '새미나이' });
      await expect(panel).toBeVisible();
      await expect(panel).toHaveCSS('right', '0px');
      await expect(launcher).toBeHidden();

      await panel.getByRole('button', { name: '새미나이 닫기' }).click();
      await expect(panel).toHaveCount(0);
      await expect(launcher).toBeVisible();
      await expect(launcher).toBeFocused();

      await launcher.click();
      await expect(panel).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(panel).toHaveCount(0);
      await expect(launcher).toBeVisible();
      await expect(launcher).toBeFocused();
    }
  });

  test('375px 홈·발표 생성의 우측 런처 여백을 확보한다', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    await page.goto('/');
    await expect(page.locator('.home-footer-content')).toHaveCSS('padding-right', '80px');
    const homeLauncher = page.locator('[data-chat-launcher] > button');
    const helpButton = page.getByRole('button', { name: '사용방법 살펴보기' });
    const homeLauncherBox = await homeLauncher.boundingBox();
    const helpBox = await helpButton.boundingBox();
    expect(homeLauncherBox).not.toBeNull();
    expect(helpBox).not.toBeNull();
    expect(overlaps(homeLauncherBox!, helpBox!)).toBe(false);
    await page.goto('/pitch/new');
    const launcher = page.locator('[data-chat-launcher] > button');
    await expect(launcher).toBeVisible();
    await expect(page.locator('.pitch-create-square > footer > div')).toHaveCSS(
      'padding-right',
      '64px',
    );
    const footerAction = page.locator('.pitch-create-square > footer').getByRole('button').last();
    const launcherBox = await launcher.boundingBox();
    const actionBox = await footerAction.boundingBox();
    expect(launcherBox).not.toBeNull();
    expect(actionBox).not.toBeNull();
    expect(overlaps(launcherBox!, actionBox!)).toBe(false);
  });

  test('리포트 하단 링크는 런처에 가리지 않는다', async ({ page }) => {
    for (const width of [375, 761, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/takes/' + TAKE_ID);
      const link = page.getByRole('link', { name: '내 피치로' });
      await expect(link).toBeVisible();
      await link.scrollIntoViewIfNeeded();
      const launcher = page.locator('[data-chat-launcher] > button');
      await expect(launcher).toBeVisible();
      const linkBox = await link.boundingBox();
      const launcherBox = await launcher.boundingBox();
      expect(linkBox).not.toBeNull();
      expect(launcherBox).not.toBeNull();
      expect(overlaps(linkBox!, launcherBox!)).toBe(false);
      await link.click({ trial: true });
    }
  });

  test('모바일에서 펼친 오류 안내는 발표 생성 푸터 위에 놓인다', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    await page.goto('/pitch/new');
    const container = page.locator('[data-chat-launcher]');
    await expect(container).toBeVisible();
    // 실제 오류 경계와 같은 aria-label로 CSS의 오류 안내 위치 조건을 확인합니다.
    await container.evaluate((element) => {
      const warning = document.createElement('section');
      warning.setAttribute('aria-label', '새미나이 오류');
      warning.style.height = '144px';
      warning.style.width = '288px';
      element.append(warning);
    });
    await expect(container).toHaveCSS('bottom', '144px');
    const footer = page.locator('.pitch-create-square > footer');
    const warningBox = await container.boundingBox();
    const footerBox = await footer.boundingBox();
    expect(warningBox).not.toBeNull();
    expect(footerBox).not.toBeNull();
    expect(warningBox!.y + warningBox!.height).toBeLessThanOrEqual(footerBox!.y);
  });

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
      // 지연 로딩 중 첫 요소가 교체될 수 있는 발표 수정 화면에서는 고유 버튼을 직접 측정합니다.
      let action = main
        .locator('a:visible, button:visible:not([disabled]):not([aria-disabled="true"])')
        .first();
      if (screen.name === '발표 수정') {
        action = main.getByRole('button', { name: /^슬라이드 확인 ·/ });
      }
      await expect(action).toBeVisible();
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

  test('1366px 패널은 헤더와 하단 바를 가리지 않는다', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.goto('/pitch/' + PITCH_ID + '/edit');
    const panel = await openPanel(page);
    const header = page.getByRole('banner').first();
    const footer = page.locator('.pitch-create-square > footer');
    await expect(header).toBeVisible();
    await expect(footer).toBeVisible();
    const panelBox = await panel.boundingBox();
    const headerBox = await header.boundingBox();
    const footerBox = await footer.boundingBox();
    expect(panelBox).not.toBeNull();
    expect(headerBox).not.toBeNull();
    expect(footerBox).not.toBeNull();
    expect(panelBox!.y).toBeGreaterThanOrEqual(headerBox!.y + headerBox!.height - 1);
    expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(footerBox!.y + 1);
    await header.getByRole('button', { name: '사용방법' }).click({ trial: true });
    await expect(footer.getByRole('button').last()).toBeInViewport();
  });

  test('1366px 스크롤 시 화면에 남은 헤더만큼 패널 상단 여백을 조정한다', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.goto('/takes');
    const header = page.locator('.app-scroll-shell > header');
    await expect(header).toBeVisible();
    const panel = await openPanel(page);

    // 헤더가 전부 보이거나, 일부만 보이거나, 화면 밖으로 나가도 빈 띠가 없어야 합니다.
    const alignedToVisibleHeader = () =>
      page.evaluate(`(() => {
        const header = document.querySelector('.app-scroll-shell > header');
        const panel = document.querySelector('[data-chat-panel]');
        if (!header || !panel) throw new Error('헤더 또는 채팅 패널이 없습니다');
        const visibleBottom = Math.max(0, Math.ceil(header.getBoundingClientRect().bottom));
        return Math.abs(panel.getBoundingClientRect().top - visibleBottom) <= 1;
      })()`);
    await expect.poll(alignedToVisibleHeader).toBe(true);
    // 스크롤 오프셋 CSS 변수는 본문 전체가 아닌 패널에만 기록해야 합니다.
    await expect
      .poll(() => panel.evaluate((element) => element.style.getPropertyValue('--chat-overlay-top')))
      .not.toBe('');
    await expect(page.locator('.chat-layout')).not.toHaveAttribute(
      'style',
      /--chat-overlay-(top|bottom)/,
    );

    // Take 기록 내용이 짧아도 테스트 환경에서 문서 스크롤을 충분히 확보합니다.
    await page.evaluate(`(() => {
      const content = document.querySelector('.chat-layout-content');
      if (!content) throw new Error('채팅 레이아웃 본문이 없습니다');
      const spacer = document.createElement('div');
      spacer.style.height = '200vh';
      content.append(spacer);
    })()`);
    await page.evaluate('window.scrollTo(0, 40)');
    await expect.poll(() => page.evaluate('window.scrollY')).toBe(40);
    await expect.poll(alignedToVisibleHeader).toBe(true);

    await page.evaluate('window.scrollTo(0, 600)');
    await expect.poll(() => page.evaluate('window.scrollY')).toBe(600);
    await expect
      .poll(() => panel.evaluate((element) => Math.round(element.getBoundingClientRect().top)))
      .toBe(0);

    await page.evaluate('window.scrollTo(0, 0)');
    await expect.poll(alignedToVisibleHeader).toBe(true);
  });

  test('761px에서 헤더와 고정 푸터 높이가 변해도 패널은 조작부를 가리지 않는다', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 761, height: 900 });
    await page.goto('/pitch/' + PITCH_ID + '/edit');
    const panel = await openPanel(page);
    const header = page.getByRole('banner').first();
    const footer = page.locator('.pitch-create-square > footer');
    await expect(header).toBeVisible();
    await expect(footer).toBeVisible();

    // 실제 메뉴가 줄바꿈되거나 안내가 길어지는 상황을 브라우저에서 재현합니다.
    await header.evaluate((element) => {
      element.style.minHeight = `${element.getBoundingClientRect().height + 75}px`;
    });
    await footer.evaluate((element) => {
      element.style.minHeight = `${element.getBoundingClientRect().height + 65}px`;
    });

    await expect
      .poll(async () => {
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
      })
      .toBe(true);
    await header.getByRole('button', { name: '사용방법' }).click({ trial: true });
    await footer.getByRole('button').last().click({ trial: true });
  });

  for (const screen of [
    { name: '분석 대기', path: '/takes/t-held/processing', action: '홈으로' },
    { name: '대본 확인', path: '/takes/t-confirm/confirm', action: '건너뛰기' },
  ]) {
    test(`1366px ${screen.name} 조작부는 열린 채팅에 가리지 않는다`, async ({ page }) => {
      await page.setViewportSize({ width: 1366, height: 900 });
      await page.goto(screen.path);
      const dialog = page.locator('.dusk-screen > main > section');
      await expect(dialog).toBeVisible();
      const panel = await openPanel(page);
      const action =
        screen.action === '홈으로'
          ? dialog.getByRole('link', { name: screen.action })
          : dialog.getByRole('button', { name: screen.action });
      await expect(action).toBeVisible();

      const panelBox = await panel.boundingBox();
      const dialogBox = await dialog.boundingBox();
      const actionBox = await action.boundingBox();
      expect(panelBox).not.toBeNull();
      expect(dialogBox).not.toBeNull();
      expect(actionBox).not.toBeNull();
      expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
      expect(actionBox!.x + actionBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
      await action.click({ trial: true });
    });
  }

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
