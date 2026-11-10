import type { MouseEvent } from 'react';

/**
 * 마우스로 누른 버튼에 초점을 남기지 않습니다. 남으면 그 뒤의 Space 를 슬라이드 키(useSlideDeck)가
 * 아니라 버튼이 받고, 마지막 장에서 막힌 버튼에 초점이 갇힙니다. Tab 으로 온 초점은 그대로 둡니다.
 */
export function keepKeyboardFocus(event: MouseEvent) {
  event.preventDefault();
}
