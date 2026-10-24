import { useEffect, useRef, type RefObject } from 'react';
import type { SlideMessage } from '../lib/sttProtocol';
import { openingSlides } from './resume';
import type { Ms } from '@/types/api';

/**
 * 장이 바뀐 시각을 WS `slide` 로 알립니다 (`docs/ai/architecture.md` 1-4, #324 6-4).
 *
 * 서버는 이 시각으로 확정 전사를 장 구간으로 나누고 실시간 코치의 "지금 장"을 정합니다. 1초마다 오는
 * `metrics.slide_number` 로는 넘긴 시각을 1초 단위로만 알아서, 넘기는 순간 근처의 말이 옆 장으로
 * 들어갈 수 있습니다.
 *
 *   발표 시작      첫 장 · `t_ms: 0`
 *   장을 넘길 때   넘어간 장 · 넘긴 순간 (`useSlideDeck` 이 `go` 에서 적어 둔 시각)
 *   이어받았을 때  장 넘김 기록 전체 · 원래 시각 (`openingSlides`)
 *
 * `useSttStream` **뒤에** 부릅니다. 첫 장을 알리는 이펙트가 소켓이 만들어진 뒤에 돌아야 합니다.
 */
export function useSlideSender({
  enabled,
  slideNumber,
  slideStartedAtRef,
  history,
  send,
}: {
  /** 발표 중일 때만 */
  enabled: boolean;
  slideNumber: number;
  /** 지금 장으로 넘어온 시각. `useSlideDeck` 이 넘길 때 적어 둡니다 */
  slideStartedAtRef: RefObject<Ms>;
  /** 새로고침해 이어받은 장 넘김 기록. 처음 시작이면 비어 있습니다 */
  history: readonly { atMs: Ms; slideNumber: number }[];
  send: (message: SlideMessage) => void;
}) {
  /** 마지막으로 알린 장. null 이면 아직 첫 장을 알리지 않았습니다 */
  const sentRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || sentRef.current === slideNumber) return;
    const first = sentRef.current === null;
    sentRef.current = slideNumber;
    if (first) {
      for (const message of openingSlides(history, slideNumber)) send(message);
      return;
    }
    send({ type: 'slide', slide_number: slideNumber, t_ms: slideStartedAtRef.current ?? 0 });
  }, [enabled, slideNumber, slideStartedAtRef, history, send]);
}
