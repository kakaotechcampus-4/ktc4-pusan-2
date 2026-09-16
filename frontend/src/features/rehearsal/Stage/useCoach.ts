import { useEffect, useRef, type RefObject } from 'react';
import { appendCoachLog } from '@/features/rehearsal/lib/db';
import type { Mode, Ms } from '@/types/api';
import { useRehearsalStore } from './rehearsalStore';

/**
 * 1단 코치 — **브라우저가 혼자 판단할 수 있는 것만** 있습니다.
 * 시선 · 음량 · 침묵 · 시간 · 슬라이드. 이 다섯은 서버가 끊겨도 계속 돕니다.
 *
 * 속도(WPM) · 군더더기 · 대본 일치 · 평가 기준은 2단(서버)입니다.
 * STT가 있어야 판단할 수 있어서 여기서 흉내내지 않습니다 —
 * 흉내내면 끊겼을 때 틀린 숫자가 발표 중에 뜹니다.
 *
 * ── 정책 ────────────────────────────────────────────────────────────
 * 한 번에 하나만, 8초. 두 개가 겹치면 둘 다 안 읽히고,
 * 발표자는 읽느라 말을 멈춥니다. 같은 종류는 1분 쿨다운입니다.
 *
 * ★ 참은 것도 남깁니다. "왜 안 떴나"의 근거가 그것뿐이고,
 *   임계값을 조정할 때 보는 것도 그 기록입니다 (suppressedFeedbacks).
 */
const DISPLAY_MS = 8_000;
/** 메시지 사이 최소 간격 */
const MIN_GAP_MS = 15_000;
/** 같은 종류의 쿨다운 */
const COOLDOWN_MS = 60_000;
/** 규칙을 훑는 주기. 판정 주기(1초)보다 짧게 볼 이유가 없습니다 */
const TICK_MS = 1_000;

/**
 * 참은 기록의 최소 간격.
 *
 * 침묵처럼 상태가 이어지는 규칙은 매 초 걸립니다. 그대로 남기면 10분에 600행이 쌓이고,
 * 정작 보고 싶은 것("이 종류가 이 즈음에 계속 걸렸다")은 그 안에 묻힙니다.
 */
const SUPPRESS_LOG_GAP_MS = 10_000;

/**
 * 임계값은 **잠정입니다.** I-17(UNCERTAIN 비율)·I-03(모델 예산)이 정해지면
 * 실측으로 바꿉니다. 지금 값은 팀 내부 Take에서 눈으로 고른 것입니다.
 */
const SILENCE_MS = 5_000;
const LOW_DB = -38;
const BOTTOM_RATIO = 0.7;
const SLIDE_STUCK_MS = 90_000;
const TIME_LEFT_WARN_MS = 60_000;

interface Rule {
  type: string;
  /** 지금 이 규칙이 걸리나. 근거가 모자라면 null을 돌려 아무 말도 하지 않습니다 */
  test: (ctx: Ctx) => string | null;
}

interface Ctx {
  elapsedMs: Ms;
  leftMs: Ms;
  db: number;
  silentMs: Ms;
  /**
   * 오디오가 실제로 흐르고 있나.
   *
   * ★ 이게 없으면 AudioContext가 suspended일 때 "말이 멈췄어요"가 계속 뜹니다 —
   *   사용자는 말하고 있는데 화면이 아니라고 우기는, 가장 나쁜 종류의 오탐입니다.
   *   소리 근거가 없으면 소리 규칙은 아예 돌리지 않습니다.
   */
  audioLive: boolean;
  bottomRatio: number | null;
  slideElapsedMs: Ms;
}

const RULES: Rule[] = [
  {
    type: 'SILENCE',
    test: (c) =>
      c.audioLive && c.silentMs > SILENCE_MS ? '말이 멈췄어요 — 다음 문장으로 넘어가 보세요' : null,
  },
  {
    type: 'TIME_OVER',
    test: (c) => (c.leftMs < 0 ? '제한 시간을 넘겼어요 — 마무리로 가세요' : null),
  },
  {
    type: 'TIME_LEFT',
    test: (c) =>
      c.leftMs > 0 && c.leftMs <= TIME_LEFT_WARN_MS ? '1분 남았어요. 마지막 장으로' : null,
  },
  {
    type: 'GAZE_BOTTOM',
    test: (c) =>
      c.bottomRatio !== null && c.bottomRatio >= BOTTOM_RATIO
        ? '화면을 오래 보고 있어요 — 고개를 들어 청중을 보세요'
        : null,
  },
  {
    type: 'LOW_VOLUME',
    test: (c) =>
      c.audioLive && c.silentMs === 0 && c.db < LOW_DB
        ? '목소리가 조금 작아요. 한 톤만 올려 보세요'
        : null,
  },
  {
    type: 'SLIDE_STUCK',
    test: (c) => (c.slideElapsedMs > SLIDE_STUCK_MS ? '이 슬라이드에 오래 머물고 있어요' : null),
  },
];

export function useCoach({
  enabled,
  mode,
  clientSessionId,
  limitSec,
  elapsedMs,
  bottomRatio,
  statsRef,
  audioLive,
  slideStartedAtRef,
}: {
  /** 발표가 도는 동안만 true. 종료 뒤에는 규칙을 돌리지 않습니다 */
  enabled: boolean;
  mode: Mode;
  clientSessionId: string | null;
  limitSec: number;
  elapsedMs: () => Ms;
  bottomRatio: (windowMs?: Ms) => number | null;
  statsRef: RefObject<{ db: number; silentMs: Ms }>;
  /** AudioContext가 running인가. 아니면 소리 규칙을 건너뜁니다 */
  audioLive: boolean;
  slideStartedAtRef: RefObject<Ms>;
}) {
  const showCoach = useRehearsalStore((s) => s.showCoach);
  const clearCoach = useRehearsalStore((s) => s.clearCoach);

  const lastFiredAtRef = useRef<Ms>(-Infinity);
  const lastByTypeRef = useRef<Record<string, Ms>>({});
  const lastSuppressLogRef = useRef<Record<string, Ms>>({});
  const hideTimerRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    const tick = () => {
      const now = elapsedMs();
      const ctx: Ctx = {
        elapsedMs: now,
        leftMs: limitSec * 1000 - now,
        db: statsRef.current?.db ?? -60,
        silentMs: statsRef.current?.silentMs ?? 0,
        audioLive,
        bottomRatio: bottomRatio(),
        slideElapsedMs: now - (slideStartedAtRef.current ?? 0),
      };

      // 위에서부터 먼저 걸리는 하나만 봅니다. 아래 규칙이 같은 초에 걸렸다면
      // 그건 다음 기회에 뜹니다 — 겹쳐 띄우지 않는 것이 정책입니다
      for (const rule of RULES) {
        const text = rule.test(ctx);
        if (text === null) continue;

        const suppressed = suppressReason(rule.type, now, mode, lastFiredAtRef, lastByTypeRef);

        const lastLog = lastSuppressLogRef.current[rule.type];
        const worthLogging =
          suppressed === null || lastLog === undefined || now - lastLog >= SUPPRESS_LOG_GAP_MS;
        if (suppressed !== null) lastSuppressLogRef.current[rule.type] = now;

        if (clientSessionId && worthLogging) {
          void appendCoachLog(clientSessionId, {
            atMs: now,
            type: rule.type,
            fired: suppressed === null,
            message: suppressed === null ? text : null,
            suppressedReason: suppressed,
          });
        }
        if (suppressed !== null) return;

        lastFiredAtRef.current = now;
        lastByTypeRef.current[rule.type] = now;
        showCoach({ type: rule.type, text, atMs: now });

        window.clearTimeout(hideTimerRef.current);
        hideTimerRef.current = window.setTimeout(clearCoach, DISPLAY_MS);
        return;
      }
    };

    const id = window.setInterval(tick, TICK_MS);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(hideTimerRef.current);
    };
  }, [
    enabled,
    mode,
    clientSessionId,
    limitSec,
    elapsedMs,
    bottomRatio,
    statsRef,
    audioLive,
    slideStartedAtRef,
    showCoach,
    clearCoach,
  ]);
}

/** 못 띄우는 이유. null이면 띄웁니다 */
function suppressReason(
  type: string,
  now: Ms,
  mode: Mode,
  lastFiredAt: RefObject<Ms>,
  lastByType: RefObject<Record<string, Ms>>,
): string | null {
  // 실전 모드는 발표 중에 아무 말도 하지 않습니다. 그래도 **기록은 남깁니다** —
  // 리포트에서 "이때 이런 게 걸렸다"를 보여 줄 수 있어야 실전 모드가 의미가 있습니다
  if (mode === 'EXAM') return 'EXAM_MODE';
  if (now - (lastFiredAt.current ?? -Infinity) < MIN_GAP_MS) return 'MIN_GAP';
  const last = lastByType.current?.[type];
  if (last !== undefined && now - last < COOLDOWN_MS) return 'COOLDOWN';
  return null;
}
