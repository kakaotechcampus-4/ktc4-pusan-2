import type { ReactNode } from 'react';

/** 본문 맨 아래 줄 — 왼쪽에 다음 할 일 한 줄, 오른쪽에 이전 · 다음 버튼 (시안 `pitch-create`) */
export function PaneFooter({ hint, children }: { hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
      <div className="text-sm text-stone">{hint}</div>
      <div className="flex gap-3">{children}</div>
    </div>
  );
}

export const primaryButton =
  'h-11 rounded-lg bg-coral px-6 text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone';
export const secondaryButton =
  'h-11 rounded-lg border border-line-strong bg-white px-5 text-sm font-bold hover:bg-panel disabled:cursor-not-allowed disabled:text-stone';
