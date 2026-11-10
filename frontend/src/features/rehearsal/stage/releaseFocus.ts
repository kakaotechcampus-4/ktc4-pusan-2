import type { MouseEvent } from 'react';

/**
 * 마우스로 슬라이드를 넘길 때 초점을 슬라이드 키(useSlideDeck)에 돌려줍니다.
 *
 * 두 가지를 합니다.
 * - 누른 요소로 초점이 옮겨 가지 않게 막습니다(`preventDefault`).
 * - **이미 다른 버튼에 있던 초점을 풉니다(`blur`).** 막기만 하면 Tab 으로 [발표 끝내기]에 둔 초점이
 *   그대로 남아, 슬라이드를 클릭한 뒤 누른 Space 가 장을 넘기지 않고 끝내기 확인을 누릅니다 (#1192 리뷰).
 *
 * 마우스를 쓰지 않으면 부르지 않으므로 Tab 으로 옮긴 초점과 그 키 조작은 그대로입니다.
 */
export function releaseFocus(event: MouseEvent) {
  event.preventDefault();
  const focused = document.activeElement;
  if (focused instanceof HTMLElement && focused !== document.body) focused.blur();
}
