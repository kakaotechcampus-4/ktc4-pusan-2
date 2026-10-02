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
          'rounded border px-3 py-3',
          gate.ready ? 'border-coral bg-coral-wash' : 'border-line-strong bg-panel',
        ].join(' ')}
      >
        {gate.ready && <p className="mb-0.5 text-[10px] text-coral-deep">{gate.heading}</p>}
        <p className="text-xs font-bold leading-snug">{gate.message}</p>

        <ul className="mt-2.5 flex flex-col gap-1.5">
          {gate.rows.map((row) => (
            <li
              key={row.key}
              className={['flex items-center gap-2 text-xs', row.done ? '' : 'text-stone'].join(
                ' ',
              )}
            >
              {/* 색만으로 구분하지 않습니다 — 체크 표시가 먼저 읽혀야 합니다 */}
              <span
                aria-hidden="true"
                className={[
                  'flex h-3.5 w-3.5 shrink-0 items-center justify-center border text-[9px] leading-none',
                  row.done ? 'border-coral bg-coral text-white' : 'border-stone bg-panel',
                ].join(' ')}
              >
                {row.done ? '✓' : ''}
              </span>
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
          'w-full rounded px-4 py-2.5 text-sm font-bold',
          gate.ready
            ? 'bg-coral text-white hover:bg-coral-deep'
            : 'cursor-not-allowed bg-line text-stone',
        ].join(' ')}
      >
        다음 →
      </button>
    </div>
  );
}
