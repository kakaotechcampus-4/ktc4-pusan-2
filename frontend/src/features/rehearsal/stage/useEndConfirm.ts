import { useCallback, useEffect, useRef, useState } from 'react';

/** 끝내기를 한 번 눌러 "정말 끝낼까요?" 로 남아 있는 시간 */
export const END_CONFIRM_MS = 4000;

/**
 * 끝내기 확인 상태. 한 번 누르면 켜지고, 시간이 지나거나 장이 바뀌면 꺼집니다.
 * 장이 바뀌면 타이머도 같이 지웁니다 — 이전 장에 갔다 시간 안에 돌아와도 처음부터 다시 묻습니다.
 * 다시 누를 때는 이전 타이머를 지우고 새로 걸어, 예전 타이머가 새 확인을 일찍 풀지 않게 합니다.
 */
export function useEndConfirm(slideNumber: number) {
  const [confirming, setConfirming] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clear = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const ask = useCallback(() => {
    clear();
    setConfirming(true);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setConfirming(false);
    }, END_CONFIRM_MS);
  }, [clear]);

  useEffect(() => {
    clear();
    // oxlint-disable-next-line react-hooks/set-state-in-effect -- 장이 바뀐 것에 맞춰 확인을 푸는 일입니다
    setConfirming(false);
    return clear;
  }, [slideNumber, clear]);

  return { confirming, ask };
}
