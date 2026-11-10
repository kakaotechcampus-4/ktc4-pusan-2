import { keepKeyboardFocus } from './keepKeyboardFocus';

/**
 * 마우스로 넘기는 이전 · 다음 버튼. 발밑 조작 안내 줄에 늘 보이게 둡니다 —
 * 마우스를 올려야 보이는 버튼은 있는 줄 몰라서 못 씁니다 (Take별 설문 피드백, 2026-11-10).
 *
 * 넘기는 일은 키보드와 같은 `go`(useSlideDeck)가 합니다. 장 넘김 기록(`appendSlideChange`)이
 * 한 경로로만 남아야 슬라이드별 체류 시간이 입력 방법과 상관없이 같습니다.
 */
export function SlideNav({
  slideNumber,
  pageCount,
  enabled,
  onGo,
}: {
  slideNumber: number;
  pageCount: number;
  enabled: boolean;
  onGo: (next: number) => void;
}) {
  return (
    <span className="slide-nav">
      <button
        type="button"
        onMouseDown={keepKeyboardFocus}
        disabled={!enabled || slideNumber <= 1}
        onClick={() => onGo(slideNumber - 1)}
      >
        ‹ 이전
      </button>
      <button
        type="button"
        onMouseDown={keepKeyboardFocus}
        disabled={!enabled || slideNumber >= pageCount}
        onClick={() => onGo(slideNumber + 1)}
      >
        다음 ›
      </button>
    </span>
  );
}
