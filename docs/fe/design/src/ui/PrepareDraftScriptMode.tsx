// PM 검토용 독립 시안. 닫힌 #789의 표시 코드를 가져왔으며 제품 모듈·API·장비와 연결하지 않는다.
import type { ScriptMode } from './ScriptModeChoice';

const OPTIONS: { value: ScriptMode; label: string; note: string }[] = [
  { value: 'HIGHLIGHT', label: '전체 대본 + 하이라이트', note: '대본 전체에 강조가 붙어요' },
  { value: 'KEYWORD', label: '핵심 키워드만', note: '문장 대신 단어만 보여요' },
  {
    value: 'OFF',
    label: '실전 모드 · 대본 없이',
    note: '대본과 실시간 코치 없이 진행해요. 기록은 리포트에서 봐요',
  },
];

export function PrepareDraftScriptMode({
  value,
  onChange,
  disabled = false,
}: {
  value: ScriptMode;
  onChange: (mode: ScriptMode) => void;
  disabled?: boolean;
}) {
  return (
    <section className="prepare-draft-script-mode">
      <div className="prepare-draft-section-heading">
        <h2>발표 중 대본 표시</h2>
        <span>시작 후 변경 불가</span>
      </div>
      <div
        role="radiogroup"
        aria-label="발표 중 대본 표시"
        aria-disabled={disabled || undefined}
        className="prepare-draft-script-options"
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
              className={['prepare-draft-script-option', on ? 'is-selected' : ''].join(' ')}
            >
              <span aria-hidden="true" className="prepare-draft-radio" />
              <span>
                <span className={`block text-sm ${on ? 'font-bold' : ''}`}>{opt.label}</span>
                <span className="sr-only">{opt.note}</span>
              </span>
            </button>
          );
        })}
      </div>
      <p className="prepare-draft-mode-note">
        실전 모드는 대본과 실시간 코치 없이 진행해요. 기록은 리포트에서 봐요.
      </p>
    </section>
  );
}
