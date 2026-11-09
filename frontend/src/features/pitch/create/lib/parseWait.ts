/**
 * 대본을 나누는 동안 기다린 시간에 따른 안내 (멘토 리뷰 #923).
 * BE 가 진행률을 내려주지 않아(`PENDING` 뿐) 기다린 시간으로 단계를 나눕니다. 보통 10초 안팎이 걸립니다.
 *
 * - 3초 전: 안내 없음 — 버튼의 "나누는 중…" 만
 * - 3초 ~ 10초: 오래 걸리는 게 정상이라고 알립니다
 * - 10초 이상: 다른 화면에 가도 결과가 이 대본에 들어간다고 알립니다(폴링은 화면 밖 스토어에서 돕니다 — `scriptParse.ts`)
 */

export const PARSE_WAIT_NORMAL_MS = 3_000;
export const PARSE_WAIT_SLOW_MS = 10_000;

export const PARSE_WAIT_TEXT = {
  normal: '장별로 나누는 중이에요(보통 10초 안팎)',
  slow: '평소보다 오래 걸리는 중이에요. 다른 화면으로 들어가도 결과는 이 대본에 들어가요',
} as const;

export function parseWaitText(elapsedMs: number): string | null {
  if (elapsedMs >= PARSE_WAIT_SLOW_MS) return PARSE_WAIT_TEXT.slow;
  if (elapsedMs >= PARSE_WAIT_NORMAL_MS) return PARSE_WAIT_TEXT.normal;
  return null;
}
