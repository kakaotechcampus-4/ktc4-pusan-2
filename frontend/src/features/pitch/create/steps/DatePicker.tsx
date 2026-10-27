import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
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
  if (selected) return 'bg-coral-deep font-bold text-white';
  if (cell.outside) return 'text-stone hover:bg-panel';
  if (cell.weekday === 0) return 'text-ink underline hover:bg-coral-wash';
  return 'text-ink hover:bg-panel';
}

function weekdayTone(weekday: number): string {
  return weekday === 0 ? 'text-ink' : 'text-stone';
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

  const [today] = useState(() => toIsoDate(new Date()));
  const [focused, setFocused] = useState(value || today);
  const grid = useRef<HTMLTableElement>(null);
  const pendingFocus = useRef(false);
  const days = monthGrid(view.year, view.month);
  const tabDate = days.some((cell) => cell.iso === focused)
    ? focused
    : toIsoDate(new Date(view.year, view.month, 1));

  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    pendingFocus.current = false;
    grid.current?.querySelector<HTMLButtonElement>(`[data-date="${focused}"]`)?.focus();
  }, [focused, view]);

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, iso: string) {
    const date = fromIsoDate(iso)!;
    let offset: number;
    switch (event.key) {
      case 'ArrowLeft':
        offset = -1;
        break;
      case 'ArrowRight':
        offset = 1;
        break;
      case 'ArrowUp':
        offset = -7;
        break;
      case 'ArrowDown':
        offset = 7;
        break;
      case 'Home':
        offset = -date.getDay();
        break;
      case 'End':
        offset = 6 - date.getDay();
        break;
      default:
        return;
    }
    event.preventDefault();
    date.setDate(date.getDate() + offset);
    pendingFocus.current = true;
    setFocused(toIsoDate(date));
    setView({ year: date.getFullYear(), month: date.getMonth() });
  }

  return (
    <div className="rounded-lg border border-line bg-white px-4 py-2">
      <div className="mb-1 flex items-center justify-between">
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

      <table
        ref={grid}
        role="grid"
        aria-label="발표 날짜"
        className="w-full table-fixed text-center"
      >
        <thead>
          <tr>
            {WEEKDAYS.map((w, i) => (
              <th key={w} scope="col" className={`py-1 text-xs font-bold ${weekdayTone(i)}`}>
                {w}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: days.length / 7 }, (_, week) => (
            <tr key={week}>
              {days.slice(week * 7, week * 7 + 7).map((cell) => {
                const selected = cell.iso === value;
                return (
                  <td key={cell.iso} aria-selected={selected} className="p-0">
                    <button
                      type="button"
                      data-date={cell.iso}
                      tabIndex={cell.iso === tabDate ? 0 : -1}
                      onFocus={() => setFocused(cell.iso)}
                      onKeyDown={(event) => moveFocus(event, cell.iso)}
                      aria-label={cell.iso}
                      aria-current={cell.iso === today ? 'date' : undefined}
                      onClick={() => onChange(cell.iso)}
                      className={`tabular mx-auto flex min-h-9 w-full max-w-10 items-center justify-center rounded-md text-sm ${dayTone(cell, selected)}`}
                    >
                      {cell.day}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
