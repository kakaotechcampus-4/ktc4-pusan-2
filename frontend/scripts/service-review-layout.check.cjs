const assert = require('node:assert/strict');
const { chromium } = require('playwright');

// Run against the mock Vite server. Install Playwright or expose it via NODE_PATH.
const baseURL = process.env.REVIEW_BASE_URL || 'http://localhost:3012';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const width of [320, 375, 768]) {
      const context = await browser.newContext({ viewport: { width, height: 800 } });
      try {
        await context.addCookies([{ name: 'mock_session', value: 'true', url: baseURL }]);
        const page = await context.newPage();
        await page.goto(`${baseURL}/takes/t3`);
        await page.getByRole('link', { name: '내 피치로', exact: true }).waitFor();
        await page.evaluate(() => sessionStorage.setItem('pitchcoach:review-preview:t3', 'ready'));
        await page.getByRole('link', { name: '내 피치로', exact: true }).click();
        const dialog = page.getByRole('dialog');
        await dialog.waitFor();
        const dimensions = await dialog.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          const left =
            bounds.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
          const right =
            bounds.right - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight);
          const logo = element.querySelector('svg').getBoundingClientRect();
          const close = element.querySelector('button').getBoundingClientRect();
          return {
            viewport: innerWidth,
            dialogLeft: bounds.left,
            dialogRight: bounds.right,
            overflow: element.scrollWidth > element.clientWidth,
            logoLeft: logo.left,
            logoRight: logo.right,
            closeLeft: close.left,
            closeRight: close.right,
            contentLeft: left,
            contentRight: right,
          };
        });
        assert.equal(dimensions.overflow, false, `${width}px: horizontal overflow`);
        assert.ok(
          dimensions.dialogLeft >= 0 && dimensions.dialogRight <= width,
          `${width}px: dialog outside viewport`,
        );
        assert.ok(
          dimensions.logoLeft >= dimensions.contentLeft - 1,
          `${width}px: logo outside content`,
        );
        assert.ok(
          dimensions.closeRight <= dimensions.contentRight + 1,
          `${width}px: close outside content`,
        );
        assert.ok(dimensions.logoRight <= dimensions.closeLeft, `${width}px: overlapping header`);
        await page.getByRole('button', { name: '리뷰 요청 닫고 계속하기' }).click();
        await page.waitForURL(`${baseURL}/`);
        console.log(`PASS ${width}px: header fits and close continues to home`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
