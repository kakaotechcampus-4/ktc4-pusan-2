import { useState } from 'react';
import { ChevronIcon } from '../icons';
import { fromIsoDate, monthGrid, toIsoDate, type CalendarDay } from '../lib/date';

/**
 * 발표 날짜 달력 — 목업 발표정보의 왼쪽 칸.
 *
 * `<input type="date">` 를 쓰지 않는 이유 — 브라우저마다 생김새가 달라 목업과 맞출 수 없고,
 * 발표일을 고르는 화면이라 한 달이 펼쳐져 있어야 "다음 주 금요일" 같은 감이 잡힙니다.
 */

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

/**
 * 일요일만 강조색으로 칠합니다. 앞뒤 달 칸은 흐리게.
 *
 * ★ 목업은 토요일을 파랑으로 칠하지만 디자인 토큰에 파랑이 없습니다 (`index.css` —
 *   "색을 새로 만들지 않습니다"). 토큰이 생기면 여기 한 줄만 더하면 됩니다.
 */
function dayTone(cell: CalendarDay, selected: boolean): string {
  if (selected) return 'bg-coral font-bold text-white';
  if (cell.outside) return 'text-line-strong hover:bg-panel';
  if (cell.weekday === 0) return 'text-coral hover:bg-coral-wash';
  return 'text-ink hover:bg-panel';
}

function weekdayTone(weekday: number): string {
  return weekday === 0 ? 'text-coral' : 'text-stone';
}

export function DatePicker({
  value,
  onChange,
}: {
  /** yyyy-mm-dd. 안 골랐으면 빈 문자열 */
  value: string;
  onChange: (iso: string) => void;
}) {
  // 고른 날이 있으면 그 달을, 없으면 이번 달을 펼칩니다
  const [view, setView] = useState(() => {
    const base = fromIsoDate(value) ?? new Date();
    return { year: base.getFullYear(), month: base.getMonth() };
  });

  const move = (delta: number) =>
    setView(({ year, month }) => {
      const d = new Date(year, month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });

  const today = toIsoDate(new Date());

  return (
    <div className="rounded-lg border border-line bg-white px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          onClick={() => move(-1)}
          aria-label="이전 달"
          className="flex h-8 w-8 items-center justify-center rounded hover:bg-panel"
        >
          <span className="rotate-180">
            <ChevronIcon up={false} />
          </span>
        </button>
        <p className="tabular text-sm font-bold" aria-live="polite">
          {view.year}년 {view.month + 1}월
        </p>
        <button
          type="button"
          onClick={() => move(1)}
          aria-label="다음 달"
          className="flex h-8 w-8 items-center justify-center rounded hover:bg-panel"
        >
          <ChevronIcon up={false} />
        </button>
      </div>

      <div role="grid" aria-label="발표 날짜" className="grid grid-cols-7 gap-y-1 text-center">
        {WEEKDAYS.map((w, i) => (
          <span key={w} role="columnheader" className={`py-1 text-xs font-bold ${weekdayTone(i)}`}>
            {w}
          </span>
        ))}

        {monthGrid(view.year, view.month).map((cell) => {
          const selected = cell.iso === value;
          return (
            <button
              key={cell.iso}
              type="button"
              role="gridcell"
              aria-selected={selected}
              aria-label={cell.iso}
              aria-current={cell.iso === today ? 'date' : undefined}
              onClick={() => onChange(cell.iso)}
              className={`tabular mx-auto flex h-8 w-10 items-center justify-center rounded-md text-sm ${dayTone(cell, selected)}`}
            >
              {cell.day}
            </button>
          );
        })}
      </div>
    </div>
  );
}
