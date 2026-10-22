/** SUPPORTED 는 이 화면 주소로 바로 들어온 경우입니다. 틀린 안내 대신 연습해도 된다고 알립니다 */
export type UnsupportedReason = 'BROWSER' | 'MOBILE' | 'SUPPORTED';

/** 기능이 없으면 그게 먼저입니다 — PC 로 옮겨도 같은 브라우저면 못 하니까요 */
export function unsupportedReason(gazeOk: boolean, touchOnly: boolean): UnsupportedReason {
  if (!gazeOk) return 'BROWSER';
  return touchOnly ? 'MOBILE' : 'SUPPORTED';
}
