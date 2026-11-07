import { useEffect, useReducer } from 'react';
import { canSend, useChatStore } from './chatStore';

/**
 * 지금 보낼 수 있는지 — 화면의 보내기 · 고르기 · 다시 보내기 잠금.
 *
 * 기다리라는 시각(429 `Retry-After` · 실행 오류의 `retry_after_s`)은 지금 시각과 견주는데, 그 시각이 지나도
 * 저장소는 바뀌지 않아 화면이 다시 그려지지 않습니다. 그래서 그 시각에 한 번 다시 그려 잠금을 풉니다
 */
export function useCanSend(): boolean {
  const blockedUntil = useChatStore((s) => s.blockedUntil);
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (blockedUntil === null) return;
    const wait = blockedUntil - Date.now();
    if (wait < 0) return;
    // 조금 넉넉히 — 타이머가 이르게 깨면 아직 막혀 있습니다
    const timer = setTimeout(redraw, wait + 50);
    return () => clearTimeout(timer);
  }, [blockedUntil]);
  return useChatStore((s) => canSend(s));
}
