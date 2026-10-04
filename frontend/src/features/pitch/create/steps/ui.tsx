import type { ReactNode } from 'react';

/** 본문 위의 제목 + 한 줄 설명. 시안 여섯 장이 같은 모양입니다 */
export function PaneHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h2 className="text-3xl font-bold">{title}</h2>
      {subtitle && <p className="mt-1 text-sm text-stone">{subtitle}</p>}
    </div>
  );
}

/** 밝은 바탕의 큰 상자. 시안의 본문은 전부 이 안에 앉습니다 */
export function Surface({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded border border-line bg-panel ${className}`.trim()}>{children}</div>
  );
}

export const primaryBtn =
  'rounded bg-coral px-5 py-2.5 text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone disabled:hover:bg-line';

export const outlineBtn =
  'rounded border border-line-strong bg-panel px-4 py-2.5 text-sm font-bold hover:bg-cream disabled:cursor-not-allowed disabled:text-stone';

/** "저장됨" 같은 상태 알약 */
export function StatusPill({ tone, children }: { tone: 'ok' | 'idle'; children: ReactNode }) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded border px-2 py-1 text-xs',
        tone === 'ok'
          ? 'border-line bg-cream/60 text-ink'
          : 'border-line-strong bg-panel text-stone',
      ].join(' ')}
    >
      {children}
    </span>
  );
}
