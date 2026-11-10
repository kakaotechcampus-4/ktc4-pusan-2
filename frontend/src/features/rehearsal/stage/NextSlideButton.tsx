import type { ReactNode } from 'react';
import { releaseFocus } from './releaseFocus';

/**
 * 오른쪽 레일의 "다음 슬라이드" — 썸네일과 안내 문구를 버튼 하나로 감싸 어디를 눌러도 다음 장입니다.
 * 넘기는 일은 키보드와 같은 `go`(useSlideDeck)가 합니다. 마지막 장에서는 레일이 이 칸을 숨깁니다.
 */
export function NextSlideButton({
  enabled,
  onNext,
  children,
}: {
  enabled: boolean;
  onNext: () => void;
  /** 썸네일. 바깥(RehearsalPage)이 PDF 쪽을 그려 넘깁니다 */
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="next-go"
      disabled={!enabled}
      onMouseDown={releaseFocus}
      onClick={onNext}
    >
      <div className="thumb">{children}</div>
      <div className="label">
        <b>다음 슬라이드</b>
        <span>누르거나 → 키로 이동</span>
      </div>
    </button>
  );
}
