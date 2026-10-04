import { useState } from 'react';
import { useCreateStore } from '../createStore';
import { buildMonth, dotted, shiftMonth } from '../lib/calendar';
import { allowedRange, formatSeconds } from '../lib/duration';
import { PaneHeading, Surface, primaryBtn } from './ui';

/**
 * 시안 — 발표정보. 제목 · 날짜 · 목표 시간 · 허용오차.
 *
 * 허용오차는 "목표보다 얼마나 짧거나 길어도 괜찮은가" 입니다. 리포트가 시간을
 * 맞췄는지 판정하는 기준이라, 값이 바뀌면 지난 Take 와의 비교 기준이 흔들립니다.
 *
 * ★ 허용오차 두 필드는 서버 계약에 아직 없습니다 (`goal_time_sec` 하나뿐).
 *   화면이 요구하는 모양을 먼저 적었고, 합의되면 `types/api.ts` 로 올립니다.
 */

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'] as const;

/** 목표 시간은 1분, 허용오차는 10초 단위로 움직입니다 */
const TARGET_STEP = 60;
const TOLERANCE_STEP = 10;
const MAX_TARGET = 60 * 60;
const MAX_TOLERANCE = 10 * 60;

function Stepper({
  label,
  value,
  step,
  min,
  max,
  onChange,
  wide = false,
}: {
  label: string;
  value: number;
  step: number;
  min: number;
  max: number;
  onChange: (next: number) => void;
  wide?: boolean;
}) {
  const btn =
    'h-11 w-11 shrink-0 rounded border border-line-strong bg-panel text-xl font-bold hover:bg-cream disabled:cursor-not-allowed disabled:text-line-strong';
  return (
    <div className="flex items-center gap-2" role="group" aria-label={label}>
      <button
        type="button"
        aria-label={`${label} 줄이기`}
        disabled={value - step < min}
        onClick={() => onChange(value - step)}
        className={btn}
      >
        −
      </button>
      <output
        aria-label={`${label} 값`}
        className={[
          'tabular flex h-11 items-center justify-center rounded bg-cream text-center text-sm font-bold',
          wide ? 'flex-1' : 'min-w-0 flex-1',
        ].join(' ')}
      >
        {formatSeconds(value)}
      </output>
      <button
        type="button"
        aria-label={`${label} 늘리기`}
        disabled={value + step > max}
        onClick={() => onChange(value + step)}
        className={btn}
      >
        +
      </button>
    </div>
  );
}

function weekdayTone(weekday: number, inMonth: boolean): string {
  // 일요일은 붉게, 토요일은 푸르게. 다른 달의 날은 색을 빼 흐리게 둡니다
  if (!inMonth) return 'text-line-strong';
  if (weekday === 0) return 'text-coral-deep';
  if (weekday === 6) return 'text-sky-700';
  return '';
}

function Calendar({ value, onPick }: { value: string; onPick: (iso: string) => void }) {
  // 선택한 날이 있으면 그 달을, 없으면 이번 달을 먼저 보여 줍니다
  const base = value ? new Date(`${value}T00:00:00`) : new Date();
  const [view, setView] = useState({ year: base.getFullYear(), month: base.getMonth() + 1 });
  const cells = buildMonth(view.year, view.month);

  const nav = 'flex h-8 w-8 items-center justify-center rounded text-lg hover:bg-cream';

  return (
    <div className="rounded border border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          aria-label="이전 달"
          onClick={() => setView((v) => shiftMonth(v.year, v.month, -1))}
          className={nav}
        >
          ‹
        </button>
        <span className="text-sm font-bold">
          {view.year}년 {view.month}월
        </span>
        <button
          type="button"
          aria-label="다음 달"
          onClick={() => setView((v) => shiftMonth(v.year, v.month, 1))}
          className={nav}
        >
          ›
        </button>
      </div>

      <div className="grid grid-cols-7 text-center text-xs">
        {WEEKDAYS.map((w, i) => (
          <span key={w} className={`py-2 font-bold ${weekdayTone(i, true)}`}>
            {w}
          </span>
        ))}
        {cells.map((c) => {
          const selected = c.iso === value;
          return (
            <button
              key={c.iso}
              type="button"
              onClick={() => onPick(c.iso)}
              aria-pressed={selected}
              aria-label={c.iso}
              className={[
                'tabular mx-auto my-0.5 flex h-9 w-9 items-center justify-center rounded text-sm',
                selected
                  ? 'bg-coral font-bold text-white'
                  : `hover:bg-cream ${weekdayTone(c.weekday, c.inMonth)}`,
              ].join(' ')}
            >
              {c.day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function InfoPane() {
  const draft = useCreateStore((s) => s.draft);
  const setMeta = useCreateStore((s) => s.setMeta);
  const saveInfo = useCreateStore((s) => s.saveInfo);

  const { minSec, maxSec } = allowedRange(
    draft.timeLimitSec,
    draft.toleranceBelowSec,
    draft.toleranceAboveSec,
  );
  const titleEmpty = draft.title.trim() === '';

  return (
    <div className="flex flex-col gap-4">
      <PaneHeading title="발표정보" subtitle="발표의 기본 정보를 설정해 주세요." />

      <Surface className="flex flex-col gap-6 p-6">
        <label className="flex flex-col gap-2">
          <span className="text-sm font-bold">발표 제목</span>
          <input
            value={draft.title}
            onChange={(e) => setMeta({ title: e.target.value })}
            placeholder="예: 캡스톤 중간발표"
            maxLength={60}
            className="h-12 rounded border border-line-strong bg-panel px-4 text-sm"
          />
        </label>

        <div className="grid gap-6 lg:grid-cols-2 lg:gap-0">
          <div className="flex flex-col gap-2 lg:pr-6">
            <span className="text-sm font-bold">발표 날짜</span>
            <Calendar
              value={draft.presentationDate}
              onPick={(iso) => setMeta({ presentationDate: iso })}
            />
            <p className="mt-1 flex items-baseline gap-3 text-xs text-stone">
              선택한 날짜
              <span className="tabular text-sm font-bold text-ink">
                {draft.presentationDate ? dotted(draft.presentationDate) : '아직 고르지 않았어요'}
              </span>
            </p>
          </div>

          <div className="flex flex-col gap-6 lg:border-l lg:border-line lg:pl-6">
            <div className="flex flex-col gap-2">
              <span className="text-sm font-bold">목표 발표시간</span>
              <Stepper
                wide
                label="목표 발표시간"
                value={draft.timeLimitSec}
                step={TARGET_STEP}
                min={TARGET_STEP}
                max={MAX_TARGET}
                onChange={(v) => setMeta({ timeLimitSec: v })}
              />
            </div>

            <div className="grid grid-cols-2 gap-6">
              <div className="flex flex-col gap-2">
                <span className="text-sm font-bold">하한 허용오차</span>
                <Stepper
                  label="하한 허용오차"
                  value={draft.toleranceBelowSec}
                  step={TOLERANCE_STEP}
                  min={0}
                  max={Math.min(MAX_TOLERANCE, draft.timeLimitSec)}
                  onChange={(v) => setMeta({ toleranceBelowSec: v })}
                />
                <p className="text-xs text-stone">목표보다 짧게 발표해도 되는 시간</p>
              </div>
              <div className="flex flex-col gap-2 border-l border-line pl-6">
                <span className="text-sm font-bold">상한 허용오차</span>
                <Stepper
                  label="상한 허용오차"
                  value={draft.toleranceAboveSec}
                  step={TOLERANCE_STEP}
                  min={0}
                  max={MAX_TOLERANCE}
                  onChange={(v) => setMeta({ toleranceAboveSec: v })}
                />
                <p className="text-xs text-stone">목표보다 길게 발표해도 되는 시간</p>
              </div>
            </div>

            <div className="mt-auto flex items-center justify-between gap-4 border-t border-line pt-4">
              <p className="text-sm">
                <span className="mr-3 text-xs text-stone">허용 범위</span>
                <span className="tabular font-bold">
                  {formatSeconds(minSec)} ~ {formatSeconds(maxSec)}
                </span>
              </p>
              <button
                type="button"
                onClick={saveInfo}
                disabled={titleEmpty || draft.infoSaved}
                className={`${primaryBtn} min-w-36`}
              >
                {draft.infoSaved ? '저장됨' : '저장'}
              </button>
            </div>
            {titleEmpty && (
              <p role="status" className="-mt-4 text-xs text-stone">
                발표 제목을 입력하면 저장할 수 있어요.
              </p>
            )}
          </div>
        </div>
      </Surface>
    </div>
  );
}
