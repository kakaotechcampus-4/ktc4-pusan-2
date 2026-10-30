import type { ScriptMode } from '@/types/api';

/**
 * 발표 중 대본을 어떻게 띄울지. **시작하면 못 바꿉니다.**
 *
 * 대본 영역 높이가 모드마다 다르고(`--spacing-script-*`), 그 높이가 바뀌면 시선 각도가
 * 바뀌어 캘리브레이션 기준이 어긋납니다 (CLAUDE.md 7번). 발표 도중에 바꾸면 그 Take 의
 * 시선 숫자가 앞뒤로 갈라져 비교가 불가능해집니다.
 *
 * 시안 09 의 세 줄입니다. 전에는 FULL(전체 대본)이 따로 있었는데 HIGHLIGHT 로
 * 합쳤습니다 — 보이는 글이 둘 다 대본 전체라 고르는 사람에게는 같은 것이 둘로
 * 보였습니다 (`types/api.ts` 의 ScriptMode 주석).
 *
 * ★ 세 번째 줄은 **대본만 끄는 것이 아닙니다.** 시안대로 실전 모드와 묶어서,
 *   고르면 발표 중 코치가 통째로 조용해집니다 (CLAUDE.md 4번). 기록은 그대로 남아
 *   리포트에서 "이때 이런 게 걸렸다"를 볼 수 있습니다 — 발표 중에만 말을 안 합니다.
 *   그 사실이 문구에 드러나야 합니다. `대본 없이` 로만 적으면 코치가 사라진 것을
 *   발표 도중에 알게 됩니다.
 */
const OPTIONS: { value: ScriptMode; label: string; note: string }[] = [
  { value: 'HIGHLIGHT', label: '전체 대본 + 하이라이트', note: '대본 전체에 강조가 붙어요' },
  { value: 'KEYWORD', label: '핵심 키워드만', note: '문장 대신 단어만 보여요' },
  {
    value: 'OFF',
    label: '실전 모드 · 대본 없이',
    note: '대본과 실시간 코치 없이 진행해요. 기록은 리포트에서 봐요',
  },
];

export function ScriptModeChoice({
  value,
  onChange,
  disabled = false,
}: {
  value: ScriptMode;
  onChange: (mode: ScriptMode) => void;
  disabled?: boolean;
}) {
  return (
    <section className="prepare-script-mode px-0.5">
      <div className="justify-between prepare-section-heading flex min-h-6 flex-wrap items-center gap-x-2.5 gap-y-1.5 text-xs leading-[18px] [&>h2]:text-[15px] [&>h2]:leading-[22px] [&>h2]:font-bold">
        <h2>발표 중 대본 표시</h2>
        <span>시작 후 변경 불가</span>
      </div>
      <div
        aria-disabled={disabled || undefined}
        role="radiogroup"
        aria-label="발표 중 대본 표시"
        className="prepare-script-options mt-1 grid grid-cols-[1.25fr_1fr_1.15fr] gap-1.5 max-[600px]:grid-cols-1"
      >
        {OPTIONS.map((opt, index) => {
          const on = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled}
              tabIndex={on ? 0 : -1}
              onKeyDown={(event) => {
                let next: number;
                switch (event.key) {
                  case 'ArrowRight':
                  case 'ArrowDown':
                    next = (index + 1) % OPTIONS.length;
                    break;
                  case 'ArrowLeft':
                  case 'ArrowUp':
                    next = (index + OPTIONS.length - 1) % OPTIONS.length;
                    break;
                  case 'Home':
                    next = 0;
                    break;
                  case 'End':
                    next = OPTIONS.length - 1;
                    break;
                  default:
                    return;
                }
                event.preventDefault();
                onChange(OPTIONS[next]!.value);
                event.currentTarget.parentElement?.querySelectorAll('button')[next]?.focus();
              }}
              onClick={() => onChange(opt.value)}
              className={`prepare-script-option flex min-h-10 min-w-0 items-center gap-1.5 border border-ink px-2 py-[5px] text-left disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink ${on ? 'is-selected bg-ink text-panel' : 'bg-panel hover:bg-cream'}`}
            >
              <span
                aria-hidden="true"
                className={`${on ? 'border-4' : 'border'} prepare-radio size-[15px] shrink-0 rounded-full border-current`}
              />
              <span>
                <span className={`block text-xs leading-4 ${on ? 'font-bold' : ''}`}>
                  {opt.label}
                </span>
                <span className="sr-only">{opt.note}</span>
              </span>
            </button>
          );
        })}
      </div>
      <p className="prepare-mode-note mt-1 text-xs leading-[18px]">
        실전 모드는 대본과 실시간 코치 없이 진행해요. 기록은 리포트에서 봐요.
      </p>
    </section>
  );
}
