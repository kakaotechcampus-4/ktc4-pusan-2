import { useCreateStore } from './createStore';
import { CalendarIcon, ClockIcon, PencilIcon } from './icons';
import { TIME_TOLERANCE_OPTIONS, type TimeToleranceSec } from './lib/draft';

/**
 * 본문 위에 떠 있는 발표 정보 줄 — 제목 · 날짜 · 발표시간.
 *
 * 발표 정보 화면에서 입력하면 그 뒤로는 어느 화면에서든 이 줄이 보입니다.
 * 슬라이드나 대본을 고치는 동안에도 "무슨 발표를 언제 하는지"가 눈앞에 있어야
 * 해서입니다. 고칠 때는 이 줄을 누르면 발표 정보 화면으로 돌아갑니다 —
 * 입력 칸을 두 곳에 두면 어느 쪽이 기준인지 흐려집니다.
 * 발표시간만은 평가기준 00번 줄에서 정하므로, 누르면 평가기준 화면으로 갑니다.
 */

/** "5분 ±1분". 오차가 `같음` 이면 붙이지 않습니다 — "±0" 은 읽는 사람이 한 번 더 셉니다 */
function timeLabel(limitSec: number, toleranceSec: TimeToleranceSec): string {
  const minutes = `${Math.round(limitSec / 60)}분`;
  if (toleranceSec === 0) return minutes;
  const tolerance = TIME_TOLERANCE_OPTIONS.find((o) => o.value === toleranceSec)?.label ?? '';
  return `${minutes} ${tolerance}`;
}

/** 발표일까지 남은 날. 지났으면 null — 배지를 그리지 않습니다 */
function daysUntil(iso: string): number | null {
  if (!iso) return null;
  const target = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
  return diff < 0 ? null : diff;
}

const chip =
  'flex h-10 min-w-0 items-center gap-2 rounded border border-line-strong bg-white px-3 text-sm hover:border-ink';

export function InfoBar() {
  const title = useCreateStore((s) => s.draft.title.trim());
  const date = useCreateStore((s) => s.draft.presentationDate);
  const limitSec = useCreateStore((s) => s.draft.timeLimitSec);
  const toleranceSec = useCreateStore((s) => s.draft.timeToleranceSec);
  const select = useCreateStore((s) => s.select);

  // 아무것도 안 넣었으면 띄우지 않습니다 — 빈 줄은 "입력해야 하나?" 하고 헷갈리게 합니다
  if (!title && !date) return null;

  const d = daysUntil(date);
  const time = timeLabel(limitSec, toleranceSec);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={() => select('info')}
        aria-label={`발표 제목 ${title || '미입력'} — 고치기`}
        className={`${chip} flex-2 basis-64 justify-between font-bold`}
      >
        <span className={['truncate', title ? '' : 'font-normal text-stone'].join(' ')}>
          {title || '제목 미입력'}
        </span>
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-coral-wash">
          <PencilIcon />
        </span>
      </button>

      <button
        type="button"
        onClick={() => select('info')}
        aria-label={`발표 날짜 ${date || '미정'} — 고치기`}
        className={`${chip} flex-1 basis-44`}
      >
        <CalendarIcon />
        <span className={['tabular', date ? '' : 'text-stone'].join(' ')}>
          {date ? date.replaceAll('-', '.') : '날짜 미정'}
        </span>
        {d !== null && (
          <span className="tabular ml-auto font-mono text-xs font-bold text-coral">D-{d}</span>
        )}
      </button>

      <button
        type="button"
        onClick={() => select('criteria')}
        aria-label={`발표시간 ${time} — 고치기`}
        className={`${chip} flex-1 basis-44`}
      >
        <ClockIcon />
        <span className="text-stone">발표시간</span>
        <span className="tabular ml-auto font-bold">{time}</span>
      </button>
    </div>
  );
}
