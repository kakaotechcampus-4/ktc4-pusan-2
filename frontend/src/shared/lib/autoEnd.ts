import type { TakeEndReason } from '@/types/api';

/**
 * 서버가 Take 를 자동으로 끝낸 이유 (#925). WS `auto_end` 의 `reason` 과 같습니다
 * (`features/rehearsal/lib/sttProtocol.ts` `AutoEndReason`).
 */
export type AutoEndReason = Extract<TakeEndReason, 'SILENCE' | 'TIME_LIMIT'>;

/** 무대가 분석 대기 화면으로 넘기는 자동 종료 안내 — 라우터 state 의 `autoEnd` 에 실립니다 */
export interface AutoEndNotice {
  reason: AutoEndReason;
  /** 서버 문구 그대로 (예: "2분 넘게 말이 없어 연습을 자동으로 끝냈어요."). 비어 있으면 FE 문구를 씁니다 */
  message: string;
}

/**
 * 말 없이 이만큼 지나면 서버가 Take 를 끝냅니다 — 시작 전 세팅에서 **미리** 알리는 데만 씁니다.
 *
 * 서버는 이 값을 미리 알려 주지 않아서(`ready` 에 없음) FE 에 둡니다.
 * BE `core/config.py` `take_silence_end_sec = 120` 과 같아야 합니다. 바뀌면 같이 고칩니다.
 * 끝난 뒤의 안내는 서버 문구(`message`)를 그대로 써서 값이 어긋나도 틀린 말을 하지 않습니다.
 *
 * 정확한 조건은 "최근 2분 동안 말이 없고, 최근 5분 중 80% 이상이 말 없음"이고 소리 크기가 아니라
 * 전사로 판단합니다 (BE `realtime/stream/auto_end.py`). 안내는 사용자가 알아야 할 "2분"만 말합니다.
 */
export const SILENCE_END_MIN = 2;

/** 시작 전 세팅의 미리 안내. 발표 목표 시간과 헷갈리지 않게 따로라고 적습니다 */
export const SILENCE_END_NOTICE = `말 없이 ${SILENCE_END_MIN}분이 지나면 연습이 자동으로 끝나요. 발표 목표 시간과는 상관없어요.`;

/** 서버 문구가 비었을 때만 씁니다 (`AutoEndPolicy.message` 와 같은 뜻) */
const FALLBACK: Record<AutoEndReason, string> = {
  SILENCE: `${SILENCE_END_MIN}분 넘게 말이 없어 연습을 자동으로 끝냈어요.`,
  TIME_LIMIT: '연습은 최대 1시간까지라 자동으로 끝냈어요.',
};

/** 이유마다 덧붙이는 말. 말 없음은 발표 목표 시간과 다른 기준이라는 것을 알립니다 */
const AFTER: Record<AutoEndReason, string> = {
  SILENCE:
    '발표 목표 시간과는 상관없이, 말이 오래 없으면 끝나요. 끝난 곳까지의 기록만 분석에 들어가요.',
  TIME_LIMIT: '끝난 곳까지의 기록만 분석에 들어가요.',
};

export function autoEndState(reason: AutoEndReason, message: string): { autoEnd: AutoEndNotice } {
  return { autoEnd: { reason, message } };
}

/**
 * 라우터 state 에서 자동 종료 안내를 꺼냅니다. 없거나 모양이 다르면 null 입니다 —
 * state 는 새로고침 · 다른 화면을 거쳐도 남아 있어서 무엇이 들어 있을지 장담할 수 없습니다.
 */
export function readAutoEnd(state: unknown): AutoEndNotice | null {
  if (typeof state !== 'object' || state === null) return null;
  const notice = (state as { autoEnd?: unknown }).autoEnd;
  if (typeof notice !== 'object' || notice === null) return null;
  const { reason, message } = notice as { reason?: unknown; message?: unknown };
  if (reason !== 'SILENCE' && reason !== 'TIME_LIMIT') return null;
  return { reason, message: typeof message === 'string' ? message : '' };
}

/** 화면에 띄울 두 줄 — 서버 문구 · 덧붙임 */
export function autoEndText(notice: AutoEndNotice): { message: string; after: string } {
  return {
    message: notice.message.trim() || FALLBACK[notice.reason],
    after: AFTER[notice.reason],
  };
}
