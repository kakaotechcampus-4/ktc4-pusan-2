/** 건너뛸 본문의 id. 상단바가 있는 화면의 `<main>` 이 이 값을 받습니다 */
export const MAIN_ID = 'main';

/**
 * 반복되는 상단바를 건너뛰어 본문으로 가는 링크. 평소에는 숨기고 Tab 으로 초점을 받을 때만 보입니다.
 * 상단바(`AppHeader`)가 맨 앞에서 한 번만 그리므로 화면마다 따로 두지 않습니다.
 *
 * 누르면 주소에 `#main` 을 붙이지 않고 본문으로 초점을 옮깁니다 (`<main tabIndex={-1}>`).
 * 다음 Tab 은 본문의 첫 요소로 갑니다.
 */
export function SkipLink() {
  return (
    <a
      href={`#${MAIN_ID}`}
      onClick={(e) => {
        // 목표가 없을 때도 기본 앵커 이동을 막습니다 — 주소에 #main 이 붙으면 안 됩니다
        e.preventDefault();
        const main = document.getElementById(MAIN_ID);
        main?.focus();
        main?.scrollIntoView();
      }}
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:bg-panel focus:p-4"
    >
      본문으로 건너뛰기
    </a>
  );
}
