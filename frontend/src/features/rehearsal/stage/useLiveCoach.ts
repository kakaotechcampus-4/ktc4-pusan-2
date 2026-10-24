import { useCallback, useEffect, useRef } from 'react';
import type { CoachMessage } from '../lib/sttProtocol';
import type { PracticeMode } from '@/types/api';
import { useRehearsalStore } from './rehearsalStore';

/** 한마디를 띄워 두는 시간. 새 한마디가 오면 바로 바꿉니다 */
const DISPLAY_MS = 8_000;

/**
 * 실시간 코치 — **AI 코치 에이전트가 판단하고, FE 는 띄우기만 합니다.**
 *
 * FE 가 1초마다 `metrics` 를 보내면 BE 가 AI `/coach/evaluate` 를 부르고, 말을 걸 때(`INTERVENE`)와
 * 상태 표시가 바뀔 때 WS `coach` 로 넘겨줍니다 (`docs/ai/architecture.md` 1-3 · 1-4, `coach-agent.md`).
 * 언제 · 얼마나 자주 말할지(쿨다운 · 효과 판정 · 우선순위)는 AI 코치가 정하므로 여기서 다시 거르지 않습니다.
 *
 * 실전 모드(`EXAM`)는 BE 가 보내지 않습니다. 혹시 와도 띄우지 않습니다 — 발표 중에 말하지 않는 것이
 * 실전 모드의 약속입니다.
 *
 * **끊기면 상태 표시를 흐리게 둡니다.** 서버는 바뀔 때만 보내서, 끊긴 뒤로는 마지막 판정이 지금 상태처럼
 * 남습니다 — 재연결을 포기하면 남은 발표 내내 경고색이 그대로입니다. 떠 있던 한마디도 거둡니다.
 * 다시 붙으면 흐림을 풉니다. BE 는 재연결 때 이전 표시를 잊고 다음 판단에서 다시 보내므로(약 1초)
 * 곧 최신 상태로 바뀝니다. 기준은 `stt_state` 가 아니라 연결입니다 — 서버의 `degraded`(Deepgram 문제)
 * 동안에도 코치는 계속 옵니다.
 *
 * @returns `useSttStream` 의 `onCoach` · `onConnection` 에 넘길 함수
 */
export function useLiveCoach({ enabled, mode }: { enabled: boolean; mode: PracticeMode }) {
  const showCoach = useRehearsalStore((s) => s.showCoach);
  const clearCoach = useRehearsalStore((s) => s.clearCoach);
  const updateIndicators = useRehearsalStore((s) => s.updateIndicators);
  const setIndicatorsStale = useRehearsalStore((s) => s.setIndicatorsStale);
  const hideTimerRef = useRef(0);

  // 발표가 끝나면(ENDING) 띄운 말을 거둡니다. 정리하는 동안 늦게 온 말도 띄우지 않습니다
  useEffect(() => {
    if (enabled) return;
    window.clearTimeout(hideTimerRef.current);
    clearCoach();
  }, [enabled, clearCoach]);

  useEffect(() => () => window.clearTimeout(hideTimerRef.current), []);

  const onConnection = useCallback(
    (connected: boolean) => {
      setIndicatorsStale(!connected);
      if (connected) return;
      window.clearTimeout(hideTimerRef.current);
      clearCoach();
    },
    [setIndicatorsStale, clearCoach],
  );

  const onCoach = useCallback(
    (message: CoachMessage) => {
      if (!enabled || mode === 'EXAM') return;

      updateIndicators(message.indicators);
      if (message.action !== 'INTERVENE' || !message.feedback) return;

      showCoach({
        area: message.feedback.area,
        instruction: message.feedback.instruction,
        text: message.feedback.message,
        atMs: message.t_ms,
      });
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = window.setTimeout(clearCoach, DISPLAY_MS);
    },
    [enabled, mode, showCoach, clearCoach, updateIndicators],
  );

  return { onCoach, onConnection };
}
