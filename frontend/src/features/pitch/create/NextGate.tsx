import { useCreateStore } from './createStore';
import { computeGate } from './lib/gate';

/**
 * 좌측 하단 상자 + "다음" 버튼.
 *
 * 규칙은 `lib/gate.ts` 에 있습니다 — 여기서는 그리기만 합니다.
 * 목업 다섯 장이 매번 다른 조합을 보여 주는데, 그 조합은 테스트가 지킵니다.
 */
export function NextGate({ onStart }: { onStart: () => void }) {
  const draft = useCreateStore((s) => s.draft);
  // 고른 버전 기준입니다 — 최신이 다 찼어도 들고 갈 것이 비었으면 열리면 안 됩니다
  const chosen = useCreateStore((s) => s.chosen);
  const gate = computeGate(draft, chosen);

  return (
    <div className="flex flex-col gap-3">
      <div
        className={[
          'rounded border px-4 py-3',
          gate.ready ? 'border-coral bg-coral-wash' : 'border-line-strong bg-panel',
        ].join(' ')}
      >
        <p className="text-xs text-stone">{gate.heading}</p>
        <p className="mt-0.5 text-sm font-bold leading-snug">{gate.message}</p>

        <ul className="mt-3 flex flex-col gap-1">
          {gate.rows.map((row) => (
            <li
              key={row.key}
              className={['flex items-center gap-1.5 text-xs', row.done ? '' : 'text-stone'].join(
                ' ',
              )}
            >
              {/* 색만으로 구분하지 않습니다 — 기호가 먼저 읽혀야 합니다 */}
              <span aria-hidden="true">{row.done ? '✓' : '○'}</span>
              <span>{row.label}</span>
            </li>
          ))}
        </ul>
      </div>

      <button
        type="button"
        disabled={!gate.ready}
        onClick={onStart}
        className={[
          'w-full rounded px-4 py-3 text-sm font-bold',
          gate.ready
            ? 'bg-coral text-panel hover:bg-coral-deep'
            : 'cursor-not-allowed bg-cream text-stone',
        ].join(' ')}
      >
        다음 ▶
      </button>
    </div>
  );
}
