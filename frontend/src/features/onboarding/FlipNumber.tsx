import { useState } from 'react';

/** 이전 숫자의 윗판이 접히고 새 숫자의 아랫판이 펼쳐지는 플립 카드. */
export function FlipNumber({ value }: { value: number }) {
  const [frame, setFrame] = useState({ current: value, previous: value });
  if (frame.current !== value) setFrame({ current: value, previous: frame.current });

  const current = String(frame.current).padStart(2, '0');
  const previous = String(frame.previous).padStart(2, '0');
  const changed = frame.current !== frame.previous;
  const half = 'absolute inset-x-0 h-1/2 overflow-hidden bg-ink text-panel';
  const digits = 'absolute inset-x-0 flex h-16 items-center justify-center sm:h-20';

  return (
    <span className="relative block h-16 rounded-md border border-ink bg-ink font-mono text-3xl font-bold tabular-nums text-panel shadow-[0_3px_0_var(--color-line-strong)] [perspective:500px] sm:h-20 sm:text-4xl">
      <span className="sr-only">{current}</span>
      <span aria-hidden="true">
        <span className={`${half} top-0 rounded-t-md`}>
          <span className={`${digits} top-0`}>{current}</span>
        </span>
        <span className={`${half} bottom-0 rounded-b-md`}>
          <span className={`${digits} bottom-0`}>{current}</span>
        </span>
        {changed && (
          <span key={current}>
            <span className={`${half} flip-old-bottom bottom-0 rounded-b-md`}>
              <span className={`${digits} bottom-0`}>{previous}</span>
            </span>
            <span className={`${half} flip-top top-0 origin-bottom rounded-t-md`}>
              <span className={`${digits} top-0`}>{previous}</span>
            </span>
            <span className={`${half} flip-bottom bottom-0 origin-top rounded-b-md`}>
              <span className={`${digits} bottom-0`}>{current}</span>
            </span>
          </span>
        )}
        <span className="pointer-events-none absolute inset-x-0 top-1/2 z-10 border-t border-panel/20" />
        <span className="absolute -left-px top-1/2 z-10 h-2 w-1 -translate-y-1/2 rounded-sm bg-stone" />
        <span className="absolute -right-px top-1/2 z-10 h-2 w-1 -translate-y-1/2 rounded-sm bg-stone" />
      </span>
    </span>
  );
}
