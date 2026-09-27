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
  { value: 'HIGHLIGHT', label: '전체 대본 + 하이라이트', note: '대본 전체에 강조가 붙습니다' },
  { value: 'KEYWORD', label: '핵심 키워드만', note: '문장 대신 단어만 남습니다' },
  {
    value: 'OFF',
    label: '실전 모드 · 대본 없이',
    note: '대본도 실시간 코치도 없습니다. 기록은 남아 리포트에서 봅니다',
  },
];

export function ScriptModeChoice({
  value,
  onChange,
}: {
  value: ScriptMode;
  onChange: (mode: ScriptMode) => void;
}) {
  return (
    <section className="rounded-xl border border-line-strong bg-panel p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-bold">발표 중 대본 표시</h2>
        <span className="text-xs text-stone">시작 후 변경 불가</span>
      </div>

      {/* 세로 목록입니다 — 넷을 가로로 놓으면 문구가 잘려 무엇이 다른지 안 보입니다 */}
      <div role="radiogroup" aria-label="발표 중 대본 표시" className="mt-3 flex flex-col gap-2">
        {OPTIONS.map((opt) => {
          const on = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(opt.value)}
              className={[
                'flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left',
                on
                  ? 'border-coral bg-coral-wash'
                  : 'border-dashed border-line-strong hover:bg-cream',
              ].join(' ')}
            >
              {/* 색만으로 고른 것을 말하지 않습니다 — 모양이 같이 바뀝니다 */}
              <span aria-hidden="true" className="mt-0.5 text-xs">
                {on ? '◉' : '○'}
              </span>
              <span>
                <span className={`block text-sm ${on ? 'font-bold' : ''}`}>{opt.label}</span>
                <span className="mt-0.5 block text-xs text-stone">{opt.note}</span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
