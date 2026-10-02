import { useCreateStore } from '../createStore';
import { MAX_TIME_LIMIT_MIN, MIN_TIME_LIMIT_MIN, TIME_TOLERANCE_OPTIONS } from '../lib/draft';

/**
 * 평가기준 00번 줄 — 발표시간과 허용 오차.
 *
 * 다른 기준과 달리 **늘 있고 지울 수 없습니다.** 시간은 모든 Take 에서 재므로
 * 버전도 없습니다 (평가기준 V2 를 만들어도 그대로입니다). 그래서 5개 한도에도 세지 않습니다.
 */

const stepButton =
  'flex h-7 w-7 items-center justify-center bg-panel text-sm font-bold hover:bg-cream disabled:cursor-not-allowed disabled:text-line-strong disabled:hover:bg-panel';

export function TimeLimitRow() {
  const timeLimitSec = useCreateStore((s) => s.draft.timeLimitSec);
  const tolerance = useCreateStore((s) => s.draft.timeToleranceSec);
  const setMeta = useCreateStore((s) => s.setMeta);

  const minutes = Math.round(timeLimitSec / 60);
  const setMinutes = (m: number) => setMeta({ timeLimitSec: m * 60 });

  return (
    <div className="flex items-stretch gap-0">
      <span className="tabular flex w-14 shrink-0 items-center justify-center rounded-l bg-coral font-mono text-xs font-bold text-white">
        00
      </span>

      <div className="flex flex-1 flex-wrap items-center gap-x-5 gap-y-2 rounded-r border border-coral bg-coral-wash px-4 py-2">
        <span className="text-sm font-bold">발표시간</span>

        <div className="ml-auto flex items-center gap-2 text-xs text-stone">
          시간
          <div className="flex items-center overflow-hidden rounded border border-ink text-ink">
            <button
              type="button"
              aria-label="발표시간 1분 줄이기"
              disabled={minutes <= MIN_TIME_LIMIT_MIN}
              onClick={() => setMinutes(minutes - 1)}
              className={stepButton}
            >
              −
            </button>
            <span
              aria-live="polite"
              className="tabular flex h-7 w-12 items-center justify-center border-x border-ink bg-white text-sm font-bold"
            >
              {minutes}분
            </span>
            <button
              type="button"
              aria-label="발표시간 1분 늘리기"
              disabled={minutes >= MAX_TIME_LIMIT_MIN}
              onClick={() => setMinutes(minutes + 1)}
              className={stepButton}
            >
              +
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-stone">
          허용 오차
          <div
            role="radiogroup"
            aria-label="허용 오차"
            className="flex overflow-hidden rounded border border-ink"
          >
            {TIME_TOLERANCE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={tolerance === opt.value}
                onClick={() => setMeta({ timeToleranceSec: opt.value })}
                className={[
                  'h-7 border-l border-ink px-3 text-xs first:border-l-0',
                  tolerance === opt.value
                    ? 'bg-ink font-bold text-white'
                    : 'bg-white text-ink hover:bg-cream',
                ].join(' ')}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
