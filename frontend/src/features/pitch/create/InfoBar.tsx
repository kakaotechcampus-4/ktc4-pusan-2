import type { ReactNode } from 'react';
import { useCreateStore } from './createStore';
import { CalendarIcon, ClockIcon, DocIcon } from './icons';
import { daysUntil, dotDate } from './lib/date';
import { formatTimeLimit } from './lib/draft';

/**
 * 본문 위에 떠 있는 발표 정보 줄 — 제목 · 날짜 · 발표시간.
 *
 * 발표정보 화면에서 저장하면 그 뒤로는 어느 화면에서든 이 줄이 보입니다.
 * 슬라이드나 대본을 고치는 동안에도 "무슨 발표를 언제 하는지"가 눈앞에 있어야
 * 해서입니다. 고칠 때는 이 줄을 누르면 발표정보 화면으로 돌아갑니다 —
 * 입력 칸을 두 곳에 두면 어느 쪽이 기준인지 흐려집니다.
 */

function Card({
  icon,
  label,
  onClick,
  children,
  className = '',
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex h-14 min-w-0 items-center gap-3 rounded-lg border border-line bg-white px-5 text-left hover:border-line-strong ${className}`}
    >
      {icon}
      <span className="shrink-0 text-xs text-stone">{label}</span>
      {children}
    </button>
  );
}

export function InfoBar() {
  const title = useCreateStore((s) => s.draft.title.trim());
  const date = useCreateStore((s) => s.draft.presentationDate);
  const limitSec = useCreateStore((s) => s.draft.timeLimitSec);
  const lowerSec = useCreateStore((s) => s.draft.lowerToleranceSec);
  const upperSec = useCreateStore((s) => s.draft.upperToleranceSec);
  const select = useCreateStore((s) => s.select);

  const d = daysUntil(date);
  const openInfo = () => select('info');

  return (
    <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1.3fr)_minmax(0,1.3fr)] gap-3">
      <Card
        icon={<DocIcon className="h-4 w-4 shrink-0 stroke-stone" />}
        label="발표 제목"
        onClick={openInfo}
      >
        <span className={['truncate font-bold', title ? '' : 'font-normal text-stone'].join(' ')}>
          {title || '제목 미입력'}
        </span>
      </Card>

      <Card icon={<CalendarIcon />} label="발표 날짜" onClick={openInfo}>
        <span className={['tabular font-bold', date ? '' : 'font-normal text-stone'].join(' ')}>
          {date ? dotDate(date) : '날짜 미정'}
        </span>
        {d !== null && (
          <span className="tabular shrink-0 rounded-md bg-coral-wash px-2 py-0.5 text-xs font-bold text-coral">
            D-{d}
          </span>
        )}
      </Card>

      <Card icon={<ClockIcon />} label="발표시간" onClick={openInfo}>
        <span className="tabular truncate font-bold">
          {formatTimeLimit(limitSec, lowerSec, upperSec)}
        </span>
      </Card>
    </div>
  );
}
