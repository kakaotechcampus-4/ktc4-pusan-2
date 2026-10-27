import type { ReactNode } from 'react';

/** 본문 맨 아래 줄 — 왼쪽에 다음 할 일 한 줄, 오른쪽에 이전 · 다음 버튼 (시안 `pitch-create`) */
export function PaneFooter({ hint, children }: { hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
      <div className="text-sm text-ink/80">{hint}</div>
      <div className="flex w-full flex-wrap gap-3 sm:w-auto">{children}</div>
    </div>
  );
}

export const primaryButton =
  'min-h-11 rounded-lg bg-coral-deep px-6 text-sm font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:bg-line disabled:text-ink/80';
export const secondaryButton =
  'h-11 rounded-lg border border-line-strong bg-white px-5 text-sm font-bold hover:bg-panel disabled:cursor-not-allowed disabled:text-ink/80';
