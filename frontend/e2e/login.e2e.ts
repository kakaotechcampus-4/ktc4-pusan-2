import { expect, test, type Page } from '@playwright/test';

/**
 * 로그인 화면의 Chrome 안내 창(#1198) E2E — 단위 시험이 흉내 낸 `showModal()` · Esc · 초점 이동을 실제 브라우저로 봅니다.
 *
 * 목 세션은 처음에 로그인돼 있어서(`playwright.config.ts`) 쿠키로 로그아웃 상태를 만듭니다(`src/mocks/auth.ts`).
 * 이 Chromium 은 브랜드가 `Chromium` · `HeadlessChrome` 뿐이라 Chrome 으로 봅니다. 다른 브라우저는
 * `navigator.userAgentData` 를 바꿔 흉내 냅니다.
 */

const NOTICE = 'PC의 Chrome에서 열어 주세요';
const GOOGLE_LOGIN = 'Google 계정으로 로그인';

test.beforeEach(async ({ context, baseURL }) => {
  await context.addCookies([{ name: 'mock_session', value: 'false', url: baseURL }]);
});

/** Edge 처럼 보이게 합니다 — Chromium 이지만 브랜드에 자기 이름을 붙입니다 */
async function pretendEdge(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgentData', {
      get: () => ({
        brands: [
          { brand: 'Microsoft Edge', version: '131' },
          { brand: 'Chromium', version: '131' },
          { brand: 'Not_A Brand', version: '24' },
        ],
        mobile: false,
        platform: 'Windows',
      }),
    });
  });
}

/** 로그인 화면을 열고 세션 확인이 끝나기를 기다립니다 — 확인하는 동안은 안내 창을 띄우지 않습니다 */
async function openLogin(page: Page) {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: '로그인' })).toBeVisible();
  await expect(page.getByRole('button', { name: GOOGLE_LOGIN })).toBeEnabled();
}

test('PC Chrome 이면 안내 창이 뜨지 않는다', async ({ page }) => {
  await openLogin(page);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('다른 브라우저면 안내 창을 띄우고, Esc 로 닫으면 Google 로그인 버튼으로 초점이 간다', async ({
  page,
}) => {
  await pretendEdge(page);
  await openLogin(page);
  const dialog = page.getByRole('dialog', { name: NOTICE });
  await expect(dialog).toContainText('시선을 재지 못할 수 있어요');
  await expect(dialog.getByRole('button', { name: '확인' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button', { name: GOOGLE_LOGIN })).toBeFocused();
});

test('확인 버튼으로 닫아도 Google 로그인 버튼으로 초점이 간다', async ({ page }) => {
  await pretendEdge(page);
  await openLogin(page);
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: GOOGLE_LOGIN })).toBeFocused();
});

test.describe('휴대폰 · 태블릿', () => {
  test.use({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 664 } });

  test('연습 화면이 열리지 않는다고 알린다', async ({ page }) => {
    await openLogin(page);
    await expect(page.getByRole('dialog', { name: NOTICE })).toContainText(
      '연습 화면이 열리지 않아요',
    );
  });
});
