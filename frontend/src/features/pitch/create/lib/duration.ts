/**
 * 발표 시간 표기. 시안은 `5분 (−30초 / +1분)` · `4분 30초 ~ 6분` 처럼
 * 0 인 단위를 적지 않습니다.
 */

/** 초 → "4분 30초" · "30초" · "6분". 0 은 "0초" */
export function formatSeconds(sec: number): string {
  const total = Math.max(0, Math.round(sec));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s}초`;
  return s === 0 ? `${m}분` : `${m}분 ${s}초`;
}

/** 허용 범위. 하한은 0 아래로 내려가지 않습니다 */
export function allowedRange(
  targetSec: number,
  belowSec: number,
  aboveSec: number,
): { minSec: number; maxSec: number } {
  return { minSec: Math.max(0, targetSec - belowSec), maxSec: targetSec + aboveSec };
}

/** 발표일까지 남은 날. 날짜가 없거나 지났으면 null — 배지를 그리지 않습니다 */
export function daysUntil(iso: string, now: Date = new Date()): number | null {
  if (!iso) return null;
  const target = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
  return diff < 0 ? null : diff;
}
