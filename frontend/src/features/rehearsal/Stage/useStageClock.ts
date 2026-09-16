import { useCallback, useEffect, useRef, useState } from 'react';
import { PresentationClock, formatDuration, remainingMs } from '@/shared/lib/clock';
import type { Ms } from '@/types/api';

/**
 * 머리줄의 시계. **숫자를 React 상태로 올리지 않습니다.**
 *
 * 1초에 한 번 렌더하면 그 렌더가 매초 시선 프레임 펌프와 같은 스레드에서 겹칩니다.
 * 바뀌는 건 글자 네 개와 막대 폭 하나뿐이라 DOM에 직접 씁니다.
 *
 * ★ 값은 매번 performance.now()에서 다시 계산합니다. tick을 세면
 *   백그라운드 탭에서 타이머가 늦춰진 만큼 시간이 실제보다 적게 흐릅니다
 *   (shared/lib/clock.ts 주석).
 *
 * tick이 250ms인 것은 초가 넘어가는 순간을 눈에 띄게 늦지 않게 잡기 위해서입니다.
 * 4Hz로 DOM 글자를 바꾸는 비용은 무시할 수 있습니다 — 렌더가 아닙니다.
 */
const TICK_MS = 250;

export function useStageClock(limitSec: number, running: boolean) {
  const [clock] = useState(() => new PresentationClock());

  const elapsedRef = useRef<HTMLSpanElement>(null);
  const limitRef = useRef<HTMLSpanElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const fillRef = useRef<HTMLElement>(null);
  const remainRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (limitRef.current) limitRef.current.textContent = formatDuration(limitSec * 1000);
  }, [limitSec]);

  useEffect(() => {
    if (!running) return;
    clock.start();

    const paint = () => {
      const elapsed = clock.elapsedMs();
      const left = remainingMs(elapsed, limitSec);
      const over = left < 0;

      if (elapsedRef.current) elapsedRef.current.textContent = formatDuration(elapsed);
      if (remainRef.current) {
        remainRef.current.textContent = over
          ? `초과 ${formatDuration(left)}`
          : `남은 ${formatDuration(left)}`;
      }
      if (fillRef.current) {
        const ratio = limitSec > 0 ? Math.min(1, elapsed / (limitSec * 1000)) : 0;
        fillRef.current.style.width = `${(ratio * 100).toFixed(1)}%`;
      }
      if (barRef.current) barRef.current.dataset.over = String(over);
    };

    paint();
    const id = window.setInterval(paint, TICK_MS);
    return () => {
      window.clearInterval(id);
      clock.stop();
    };
  }, [clock, limitSec, running]);

  // 다른 층(코치 규칙·슬라이드 기록·종료 페이로드)이 같은 기준으로 읽습니다.
  // ★ 함수 정체성을 고정합니다 — 매 렌더 새로 만들면 이걸 의존성으로 쓰는
  //   코치 인터벌이 렌더마다 다시 걸리고, 그때마다 주기가 처음부터 다시 셉니다.
  const elapsedMs = useCallback((): Ms => clock.elapsedMs(), [clock]);

  // ref 를 객체로 싸서 돌려주지 않습니다 — 받는 쪽이 `clock.refs.x` 로 읽으면
  // 렌더 중에 ref 를 만지는 모양이 되고, React Compiler 가 그걸 잡습니다.
  return { elapsedRef, limitRef, barRef, fillRef, remainRef, elapsedMs, clock };
}
