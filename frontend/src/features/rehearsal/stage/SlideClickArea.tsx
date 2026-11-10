import { keepKeyboardFocus } from './keepKeyboardFocus';

/**
 * 슬라이드를 마우스로 넘기는 칸 — 좌클릭은 다음 장, 우클릭은 이전 장입니다. 파워포인트 슬라이드 쇼에서
 * 오른쪽 클릭 메뉴를 끈 것과 같습니다 (Take별 설문 피드백, 2026-11-10).
 *
 * 슬라이드 위에 투명하게 겹쳐 두어 칸의 크기 · 자리(시선 기준)는 바꾸지 않습니다. 화면에 버튼을 따로
 * 두지 않는 대신 발밑 조작 안내 줄에 "클릭 · 우클릭"을 적습니다. 키보드(← → · Space)가 있어 Tab 대상에서는 뺍니다.
 *
 * 넘기는 일은 키보드와 같은 `go`(useSlideDeck)가 합니다. 장 넘김 기록(`appendSlideChange`)이 한 경로로만
 * 남아야 슬라이드별 체류 시간이 입력 방법과 상관없이 같습니다. 첫 장 · 마지막 장에서 멈추는 것도 `go` 가 합니다.
 *
 * 막아 두지(disabled) 않습니다 — 막힌 버튼은 우클릭을 받지 못해 마지막 장에서 이전 장으로 갈 수 없습니다.
 */
export function SlideClickArea({
  slideNumber,
  enabled,
  onGo,
}: {
  slideNumber: number;
  enabled: boolean;
  onGo: (next: number) => void;
}) {
  return (
    <button
      type="button"
      className="slide-advance"
      tabIndex={-1}
      aria-hidden="true"
      data-enabled={enabled}
      onMouseDown={keepKeyboardFocus}
      onClick={() => {
        if (enabled) onGo(slideNumber + 1);
      }}
      onContextMenu={(event) => {
        // 발표 중 슬라이드 위에서는 브라우저 메뉴를 띄우지 않습니다. 시작 전에는 그대로 둡니다
        if (!enabled) return;
        event.preventDefault();
        onGo(slideNumber - 1);
      }}
    />
  );
}
