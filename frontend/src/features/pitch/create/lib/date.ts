/**
 * 발표 날짜 계산. 날짜는 전부 **로컬 날짜 문자열**(`yyyy-mm-dd`)로 다룹니다.
 *
 * `Date` 를 그대로 들고 다니지 않는 이유 — `toISOString()` 은 UTC 라서 한국 시간
 * 오전 9시 전에는 하루 전 날짜가 나옵니다. 서버의 `presentation_date: date` 도 시각이 없습니다.
 */

/** 로컬 기준 yyyy-mm-dd */
export function toIsoDate(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** "2026-10-09" → 그 날 0시(로컬). 형식이 틀리면 null */
export function fromIsoDate(iso: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "2026-10-09" → "2026.10.09" */
export const dotDate = (iso: string) => iso.replaceAll('-', '.');

/** 발표일까지 남은 날. 지났거나 없으면 null — 배지를 그리지 않습니다 */
export function daysUntil(iso: string, today: Date = new Date()): number | null {
  const target = fromIsoDate(iso);
  if (!target) return null;
  const base = new Date(today);
  base.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - base.getTime()) / 86_400_000);
  return diff < 0 ? null : diff;
}

export interface CalendarDay {
  iso: string;
  day: number;
  /** 0 = 일요일 … 6 = 토요일 */
  weekday: number;
  /** 앞뒤 달에서 빌려 온 칸. 흐리게 그립니다 */
  outside: boolean;
}

/**
 * 한 달 달력 칸. 일요일부터 시작하고, 앞뒤 달 날짜로 주를 채웁니다.
 * 주 수는 달마다 4~6주로 달라집니다 — 빈 주를 덧붙이지 않습니다.
 *
 * @param month 0 = 1월
 */
export function monthGrid(year: number, month: number): CalendarDay[] {
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - first.getDay());
  const last = new Date(year, month + 1, 0);
  const end = new Date(year, month, last.getDate() + (6 - last.getDay()));

  const days: CalendarDay[] = [];
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    days.push({
      iso: toIsoDate(d),
      day: d.getDate(),
      weekday: d.getDay(),
      outside: d.getMonth() !== month,
    });
  }
  return days;
}
