import { useCreateStore } from './createStore';

/**
 * 상단 세 칸 — 제목 · 발표 날짜 · 제한 시간. 그리고 "수정 불가" 토글.
 *
 * 목업에서 이 줄은 다섯 장 내내 같은 자리에 있습니다. 슬라이드를 고치든 대본을
 * 고치든 이 셋은 피치 한 판에 하나뿐이라, 버전 트리 밖에 둡니다.
 *
 * "수정 불가"는 실수로 값이 바뀌는 것을 막는 잠금입니다 — 발표 날짜나 제한 시간이
 * 바뀌면 지난 Take 와의 비교 기준이 흔들립니다.
 */

/** 초 → "5분". 목업 표기 */
function minutesLabel(sec: number): string {
  const m = Math.round(sec / 60);
  return `${m}분`;
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

export function MetaBar({ locked, onToggleLock }: { locked: boolean; onToggleLock: () => void }) {
  const draft = useCreateStore((s) => s.draft);
  const setMeta = useCreateStore((s) => s.setMeta);

  const d = daysUntil(draft.presentationDate);

  const field = [
    'h-12 w-full rounded border bg-panel px-4 text-sm',
    locked ? 'border-line text-stone' : 'border-ink',
  ].join(' ');

  return (
    <div className="flex items-start gap-3">
      <input
        aria-label="발표 제목"
        placeholder="발표 제목"
        value={draft.title}
        disabled={locked}
        onChange={(e) => setMeta({ title: e.target.value })}
        className={`${field} flex-[2] font-bold`}
      />

      <div className="relative flex-1">
        <input
          aria-label="발표 날짜"
          type="date"
          value={draft.presentationDate}
          disabled={locked}
          onChange={(e) => setMeta({ presentationDate: e.target.value })}
          className={field}
        />
        {d !== null && (
          <span className="tabular pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 font-mono text-xs text-coral">
            D-{d}
          </span>
        )}
      </div>

      <input
        aria-label="발표 제한시간 (분)"
        type="number"
        min={1}
        max={60}
        value={Math.round(draft.timeLimitSec / 60)}
        disabled={locked}
        onChange={(e) => setMeta({ timeLimitSec: Math.max(1, Number(e.target.value)) * 60 })}
        placeholder={minutesLabel(draft.timeLimitSec)}
        className={`${field} flex-1 tabular`}
      />

      <button
        type="button"
        onClick={onToggleLock}
        aria-pressed={locked}
        className="h-12 shrink-0 rounded border border-line-strong bg-panel px-3 text-xs leading-tight text-stone hover:text-ink"
      >
        수정
        <br />
        {locked ? '불가' : '가능'}
      </button>
    </div>
  );
}
