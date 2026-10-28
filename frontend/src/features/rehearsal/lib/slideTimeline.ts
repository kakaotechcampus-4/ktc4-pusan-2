/**
 * 슬라이드 전환 기록 — "그 시각에 몇 번 슬라이드였나"를 답합니다.
 *
 * WS `metrics` 는 그 1초가 끝나고 늦게 나갑니다. 시선 기록이 오면 0.1~0.3초, 시선이 없어 스스로
 * 잇는 1초는 `GAZE_WAIT_MS` 를 기다려 2~3초 뒤입니다(`metricsClock`). 보내는 순간의 번호를 붙이면
 * 넘길 때마다 직전 몇 초가 다음 슬라이드로 적힙니다. 그래서 그 1초가 **시작한 시각**의 번호를 붙입니다.
 *
 * 전환 시각은 넘기는 쪽(`useSlideDeck.go`)이 무대 시계로 적습니다. 한 Take 에 많아야 수백 번이라
 * 지우지 않습니다. 시계와 React 를 모릅니다 — 그래서 브라우저 없이 테스트합니다.
 */
import type { Ms } from '@/types/api';

export interface SlideTimeline {
  /** `atMs` 에 `slide` 로 넘겼습니다. 무대 시계라 시각 순으로 옵니다 */
  change(atMs: Ms, slide: number): void;
  /** `ms` 에 보이던 슬라이드. 넘긴 바로 그 시각은 넘긴 뒤의 번호입니다 */
  at(ms: Ms): number;
}

/** @param initial 무대가 열릴 때 보이던 슬라이드 (새로고침해 이어받았으면 되살린 번호) */
export function createSlideTimeline(initial: number): SlideTimeline {
  // 시각 순. 첫 항목은 처음부터 보이던 슬라이드입니다
  const changes: { atMs: Ms; slide: number }[] = [{ atMs: -Infinity, slide: initial }];

  return {
    change(atMs, slide) {
      changes.push({ atMs, slide });
    },

    at(ms) {
      for (let i = changes.length - 1; i > 0; i--) {
        if (changes[i]!.atMs <= ms) return changes[i]!.slide;
      }
      return changes[0]!.slide;
    },
  };
}
