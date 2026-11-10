/**
 * 지금 기기 · 브라우저가 어떤 환경인지 봅니다. 브라우저가 스스로 밝히는 값이라 안내 · 화면 분기에만 씁니다.
 */

/**
 * 휴대폰 · 태블릿처럼 손가락으로만 쓰는 기기인가.
 *
 * 화면 폭이 아니라 입력 장치로 가립니다 — 좁은 창으로 연 노트북은 막지 않고, 폭이 넓은 태블릿은 막기 위해서입니다.
 * 주 입력이 터치(`pointer: coarse`)이면서 마우스 · 트랙패드가 하나도 없을 때(`any-pointer: fine` 없음)만 해당합니다.
 * 터치스크린이 달린 노트북은 주 입력이 마우스 · 트랙패드라 걸리지 않고, 마우스를 물린 태블릿도 풀립니다.
 * matchMedia 가 없는 환경(테스트 · 서버)은 막지 않습니다.
 */
export function isTouchOnlyDevice(
  matchMedia: ((query: string) => { matches: boolean }) | undefined = globalThis.matchMedia?.bind(
    globalThis,
  ),
): boolean {
  if (!matchMedia) return false;
  return matchMedia('(pointer: coarse)').matches && !matchMedia('(any-pointer: fine)').matches;
}

/** `navigator.userAgentData` 에서 쓰는 칸. TS 기본 타입(lib.dom)에는 아직 없습니다 */
interface UserAgentData {
  readonly brands: ReadonlyArray<{ readonly brand: string }>;
  readonly mobile: boolean;
  readonly platform: string;
}

/** 판별에 쓰는 `navigator` 의 일부. 테스트가 브라우저마다 값을 넣어 봅니다 */
export interface BrowserIdentity {
  readonly userAgent: string;
  readonly userAgentData?: UserAgentData;
}

/** 브라우저가 브랜드 목록에 섞어 넣는 가짜 이름. "Not.A/Brand" · "Not_A Brand" 처럼 글자 사이 기호가 바뀝니다 */
const GREASE_BRAND = /not.?a.?brand/i;

/** 다른 회사 이름이 붙지 않은 Chromium. Playwright E2E 의 내장 브라우저가 이 둘만 냅니다 */
const PLAIN_CHROMIUM = new Set(['Chromium', 'HeadlessChrome']);

/** UA 문자열에 `Chrome/` 이 있어도 PC Chrome 이 아닌 브라우저 · 기기의 표시 */
const NOT_DESKTOP_CHROME_UA = /Edg(A|iOS)?\/|OPR\/|Whale\/|SamsungBrowser\/|YaBrowser\/|Android/;

/**
 * PC 의 Google Chrome 인가. 시선 분석은 PC Chrome 에서만 확인했습니다 (#1178 · #1198).
 *
 * `userAgentData` 의 브랜드를 먼저 봅니다. Edge · 웨일 · 오페라 · 브레이브는 Chromium 이지만 자기 이름을 붙여서
 * 여기서 갈립니다. `userAgentData` 가 없으면(Safari · Firefox, https 가 아닌 주소로 연 Chrome) UA 문자열로 봅니다.
 * 휴대폰 · 태블릿은 브라우저와 상관없이 PC 가 아닙니다.
 */
export function isDesktopChrome(
  nav: BrowserIdentity | undefined = globalThis.navigator as BrowserIdentity | undefined,
  touchOnly: boolean = isTouchOnlyDevice(),
): boolean {
  if (!nav || touchOnly) return false;
  const data = nav.userAgentData;
  // 브랜드가 빈 목록이면(UA 를 바꿔 다른 브라우저를 흉내 낸 경우 등) 판단할 근거가 없어 UA 문자열로 봅니다
  if (data && data.brands.length > 0) {
    if (data.mobile || data.platform === 'Android') return false;
    const names = data.brands
      .map(({ brand }) => brand)
      .filter((brand) => !GREASE_BRAND.test(brand));
    if (names.includes('Google Chrome')) return true;
    return names.length > 0 && names.every((brand) => PLAIN_CHROMIUM.has(brand));
  }
  return /Chrome\/\d/.test(nav.userAgent) && !NOT_DESKTOP_CHROME_UA.test(nav.userAgent);
}
