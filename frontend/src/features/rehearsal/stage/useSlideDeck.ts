import { useCallback, useEffect, useRef } from 'react';
import { appendSlideChange } from '../lib/db';
import { noteWriteFailure } from '../lib/writeFailures';
import type { Ms } from '@/types/api';
import { useRehearsalStore } from './rehearsalStore';

/**
 * 슬라이드 넘기기와 그 기록.
 *
 * 리포트의 Finding은 "몇 번 슬라이드에서"를 말해야 의미가 있습니다.
 * 그 근거가 여기 남는 전환 시각뿐입니다 — 발표 뒤에는 되살릴 방법이 없습니다.
 *
 * 키는 ←/→ 입니다. 스페이스도 다음으로 받습니다(리모컨이 스페이스를 보냅니다).
 * 대본 스크롤은 ↑/↓ 이고, 그건 화면 쪽에서 대본 엘리먼트에 직접 겁니다.
 */
export function useSlideDeck({
  total,
  clientSessionId,
  elapsedMs,
  enabled,
}: {
  total: number;
  clientSessionId: string | null;
  elapsedMs: () => Ms;
  enabled: boolean;
}) {
  const slideNumber = useRehearsalStore((s) => s.slideNumber);
  const setSlide = useRehearsalStore((s) => s.setSlide);

  /** 지금 슬라이드에 머문 시간을 코치 규칙이 읽습니다 */
  const slideStartedAtRef = useRef<Ms>(0);

  const go = useCallback(
    (next: number) => {
      // 지금 값은 스토어에서 바로 읽습니다. 렌더에서 ref 에 옮겨 두면
      // 그 자체가 "렌더 중 ref 접근"이 되고, 한 박자 늦은 값을 보게 됩니다
      const clamped = Math.min(Math.max(1, next), Math.max(1, total));
      if (clamped === useRehearsalStore.getState().slideNumber) return;

      const atMs = elapsedMs();
      slideStartedAtRef.current = atMs;
      setSlide(clamped);
      // 전환 하나가 빠지면 그 슬라이드의 체류 시간이 앞 슬라이드에 합산됩니다 —
      // 서버가 받는 타임라인이 조용히 달라지므로 버리지 않고 셉니다
      if (clientSessionId) {
        appendSlideChange(clientSessionId, atMs, clamped).catch((err: unknown) =>
          noteWriteFailure(clientSessionId, 'slideChange', err),
        );
      }
    },
    [total, elapsedMs, setSlide, clientSessionId],
  );

  // 첫 장도 기록합니다. 없으면 발표 시작부터 첫 전환까지가 어느 슬라이드였는지
  // 서버가 알 수 없습니다 (0ms 행이 그 구간의 시작입니다)
  useEffect(() => {
    if (!enabled || !clientSessionId) return;
    slideStartedAtRef.current = 0;
    // 이 0ms 행이 타임라인의 시작점입니다. 빠지면 첫 전환 전까지가
    // 어느 슬라이드였는지 서버가 알 방법이 없습니다
    appendSlideChange(clientSessionId, 0, useRehearsalStore.getState().slideNumber).catch(
      (err: unknown) => noteWriteFailure(clientSessionId, 'slideChange', err),
    );
  }, [enabled, clientSessionId]);

  useEffect(() => {
    if (!enabled) return;

    const onKey = (e: KeyboardEvent) => {
      // 입력 중에는 넘기지 않습니다 — 발표 중에 입력 칸은 없지만,
      // 종료 확인 같은 대화상자가 뜨면 그쪽이 키를 가져가야 합니다
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;

      const now = useRehearsalStore.getState().slideNumber;
      if (e.key === 'ArrowRight' || e.key === ' ') {
        e.preventDefault();
        go(now + 1);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        go(now - 1);
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled, go]);

  return { slideNumber, go, slideStartedAtRef };
}
