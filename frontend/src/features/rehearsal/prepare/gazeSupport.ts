/**
 * 이 브라우저에서 시선 엔진이 돌 수 있나. 카메라 권한을 묻기 **전에** 봅니다 (AI `DEPLOY.md`).
 *
 * 조건은 AI 엔진의 `checkSupport()`(`vendor/gaze/engine/index.ts`)와 같습니다. 그 함수를 직접
 * 부르지 않는 이유는 엔진 진입점이 MediaPipe 를 함께 끌고 와서, 메인 화면 번들이 약 150KB
 * 커지기 때문입니다 (엔진은 워커 안에서만 돕니다). 대신 두 결과가 같은지 테스트가 지킵니다 —
 * AI 가 조건을 바꾸면 `gazeSupport.test.ts` 가 깨집니다.
 *
 * 워커가 있는지는 엔진이 보지 않고 우리가 봅니다 (엔진 주석: "The host checks `Worker` itself").
 */
export interface GazeSupport {
  ok: boolean;
  /** 없는 브라우저 기능. 로그에만 남깁니다 */
  missing: string[];
}

export function checkGazeSupport(): GazeSupport {
  const missing: string[] = [];
  if (typeof WebAssembly !== 'object') missing.push('WebAssembly');
  if (typeof createImageBitmap !== 'function') missing.push('createImageBitmap');
  if (typeof Worker !== 'function') missing.push('Worker');
  return { ok: missing.length === 0, missing };
}
