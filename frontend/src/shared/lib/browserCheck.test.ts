import { expect, it } from 'vitest';
import { isDesktopChrome, isTouchOnlyDevice, type BrowserIdentity } from './browserCheck';

const media = (matching: string[]) => (query: string) => ({ matches: matching.includes(query) });

it('손가락뿐인 기기(휴대폰 · 태블릿)는 터치 전용이다', () => {
  expect(isTouchOnlyDevice(media(['(pointer: coarse)']))).toBe(true);
});

it('마우스 · 트랙패드가 있으면 터치 전용이 아니다 (노트북 · 키보드를 단 태블릿)', () => {
  expect(isTouchOnlyDevice(media(['(pointer: fine)', '(any-pointer: fine)']))).toBe(false);
  expect(isTouchOnlyDevice(media(['(pointer: coarse)', '(any-pointer: fine)']))).toBe(false);
});

it('matchMedia 가 없으면 막지 않는다', () => {
  expect(isTouchOnlyDevice(undefined)).toBe(false);
});

const CHROME_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const SAFARI_MAC =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15';
const FIREFOX_MAC =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0';
const WHALE_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Whale/4.29.282.14 Safari/537.36';
const CHROME_ANDROID =
  'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
const CHROME_IPHONE =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1';

const withBrands = (brands: string[], platform = 'Windows', mobile = false): BrowserIdentity => ({
  userAgent: CHROME_WINDOWS,
  userAgentData: { brands: brands.map((brand) => ({ brand })), mobile, platform },
});

it('브랜드에 Google Chrome 이 있으면 PC Chrome 이다', () => {
  expect(isDesktopChrome(withBrands(['Google Chrome', 'Chromium', 'Not_A Brand']), false)).toBe(
    true,
  );
  expect(
    isDesktopChrome(withBrands(['Not A(Brand', 'Chromium', 'Google Chrome'], 'macOS'), false),
  ).toBe(true);
});

it('Edge · 웨일 · 오페라 · 브레이브는 Chromium 이어도 Chrome 이 아니다', () => {
  for (const vendor of ['Microsoft Edge', 'Whale', 'Opera', 'Brave'])
    expect(isDesktopChrome(withBrands([vendor, 'Chromium', 'Not_A Brand']), false)).toBe(false);
});

it('다른 이름이 없는 Chromium(E2E 내장 브라우저)은 Chrome 으로 본다', () => {
  // Playwright 1.65 내장 Chromium 이 http://localhost 에서 실제로 낸 값입니다
  expect(isDesktopChrome(withBrands(['Chromium', 'Not.A/Brand', 'HeadlessChrome']), false)).toBe(
    true,
  );
  expect(isDesktopChrome(withBrands(['Not.A/Brand']), false)).toBe(false);
});

it('휴대폰 · 태블릿은 Chrome 이어도 PC Chrome 이 아니다', () => {
  const android: BrowserIdentity = {
    userAgent: CHROME_ANDROID,
    userAgentData: {
      brands: [{ brand: 'Google Chrome' }, { brand: 'Chromium' }],
      mobile: true,
      platform: 'Android',
    },
  };
  expect(isDesktopChrome(android, false)).toBe(false);
  // '데스크톱 사이트 보기' 로 PC 처럼 보여도 손가락뿐인 기기면 걸립니다
  expect(isDesktopChrome(withBrands(['Google Chrome', 'Chromium'], 'Linux'), true)).toBe(false);
});

it('터치스크린 노트북의 Chrome 은 PC Chrome 이다', () => {
  const touchLaptop = isTouchOnlyDevice(media(['(pointer: fine)', '(any-pointer: fine)']));
  expect(isDesktopChrome(withBrands(['Google Chrome', 'Chromium']), touchLaptop)).toBe(true);
});

it('userAgentData 가 없거나 브랜드가 비면 UA 문자열로 본다', () => {
  // https 가 아닌 주소로 연 Chrome 은 userAgentData 가 없습니다
  expect(isDesktopChrome({ userAgent: CHROME_WINDOWS }, false)).toBe(true);
  for (const userAgent of [
    SAFARI_MAC,
    FIREFOX_MAC,
    WHALE_WINDOWS,
    `${CHROME_WINDOWS} Edg/131.0.0.0`,
    CHROME_ANDROID,
    CHROME_IPHONE,
  ])
    expect(isDesktopChrome({ userAgent }, false)).toBe(false);
  const emulated: BrowserIdentity = {
    userAgent: SAFARI_MAC,
    userAgentData: { brands: [], mobile: false, platform: '' },
  };
  expect(isDesktopChrome(emulated, false)).toBe(false);
});

it('navigator 가 없으면 Chrome 으로 보지 않는다', () => {
  expect(isDesktopChrome(undefined, false)).toBe(false);
});
