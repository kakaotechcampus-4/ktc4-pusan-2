// 제품 `frontend/src/features/rehearsal/prepare/ScriptModeChoice.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { Card } from './Panel';

export type ScriptMode = 'HIGHLIGHT' | 'KEYWORD' | 'OFF';

/**
 * 발표 중 대본을 어떻게 띄울지. 시작하면 못 바꾼다 — 대본 칸 높이가 모드마다 달라 시선 각도가 바뀐다.
 * 세 번째 줄은 대본만 끄는 것이 아니라 실전 모드(코치도 조용함)라 그 사실을 문구에 적는다.
 */
const SCRIPT_MODES: { value: ScriptMode; label: string; note: string }[] = [
  { value: 'HIGHLIGHT', label: '전체 대본 + 하이라이트', note: '대본 전체에 강조가 붙습니다' },
  { value: 'KEYWORD', label: '핵심 키워드만', note: '문장 대신 단어만 남습니다' },
  {
    value: 'OFF',
    label: '실전 모드 · 대본 없이',
    note: '대본도 실시간 코치도 없습니다. 기록은 남아 리포트에서 봅니다',
  },
];

/** 선택 카드 묶음(radiogroup). 화살표 · Home · End로 옮기고, 고른 것만 Tab으로 들어간다 */
export function ScriptModeChoice({
  value,
  onChange,
  disabled = false,
}: {
  value: ScriptMode;
  onChange: (mode: ScriptMode) => void;
  /** 시작을 누른 뒤에는 잠근다 */
  disabled?: boolean;
}) {
  return (
    <Card
      title="발표 중 대본 표시"
      action={<span className="text-xs text-stone">시작 후 변경 불가</span>}
    >
      <div
        role="radiogroup"
        aria-label="발표 중 대본 표시"
        aria-disabled={disabled || undefined}
        className="flex flex-col gap-2"
      >
        {SCRIPT_MODES.map((opt, index) => {
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
                    next = (index + 1) % SCRIPT_MODES.length;
                    break;
                  case 'ArrowLeft':
                  case 'ArrowUp':
                    next = (index + SCRIPT_MODES.length - 1) % SCRIPT_MODES.length;
                    break;
                  case 'Home':
                    next = 0;
                    break;
                  case 'End':
                    next = SCRIPT_MODES.length - 1;
                    break;
                  default:
                    return;
                }
                event.preventDefault();
                onChange(SCRIPT_MODES[next]!.value);
                event.currentTarget.parentElement?.querySelectorAll('button')[next]?.focus();
              }}
              onClick={() => onChange(opt.value)}
              className={[
                'flex items-start gap-3 border-2 px-3 py-2.5 text-left disabled:cursor-not-allowed disabled:opacity-60',
                on ? 'border-ink bg-coral-wash' : 'border-line-strong bg-panel hover:bg-cream',
              ].join(' ')}
            >
              {/* 색만으로 고른 것을 말하지 않는다 — 모양이 같이 바뀐다 */}
              <span aria-hidden="true" className="mt-0.5 text-xs">
                {on ? '◉' : '○'}
              </span>
              <span>
                <span className={`block text-sm ${on ? 'font-bold' : ''}`}>{opt.label}</span>
                <span className="mt-0.5 block text-xs text-ink/80">{opt.note}</span>
              </span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}
