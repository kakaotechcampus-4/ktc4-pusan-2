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
