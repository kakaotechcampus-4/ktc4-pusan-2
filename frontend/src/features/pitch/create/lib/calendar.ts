/**
 * 발표정보 화면의 달력. 일요일 시작 6주(42칸) 고정입니다 — 달마다 줄 수가 달라지면
 * 이전·다음을 누를 때 아래 요소가 위아래로 출렁입니다.
 */

export interface CalendarCell {
  /** ISO yyyy-mm-dd */
  iso: string;
  day: number;
  /** 보고 있는 달 안의 날인가. 아니면 흐리게 보입니다 */
  inMonth: boolean;
  /** 0 = 일요일 */
  weekday: number;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** `month` 는 1~12 */
export function buildMonth(year: number, month: number): CalendarCell[] {
  const first = new Date(year, month - 1, 1);
  const start = new Date(year, month - 1, 1 - first.getDay());

  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    return {
      iso: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      day: d.getDate(),
      inMonth: d.getMonth() === month - 1,
      weekday: d.getDay(),
    };
  });
}

/** 달 이동. 12월 다음은 이듬해 1월입니다 */
export function shiftMonth(year: number, month: number, delta: number) {
  const d = new Date(year, month - 1 + delta, 1);
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

/** "2026-10-09" → "2026.10.09" */
export function dotted(iso: string): string {
  return iso.replaceAll('-', '.');
}
