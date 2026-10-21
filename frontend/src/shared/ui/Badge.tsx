import type { ReactNode } from 'react';

export type Tone = 'bad' | 'good' | 'strong' | 'plain' | 'muted';

const TONE: Record<Tone, string> = {
  bad: 'bg-coral text-white',
  good: 'bg-coral-wash text-coral-deep',
  strong: 'bg-ink text-panel',
  plain: 'bg-cream text-ink',
  muted: 'border border-dashed border-line-strong text-stone',
};

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`w-fit shrink-0 px-2 py-0.5 text-xs font-bold ${TONE[tone]}`}>{children}</span>
  );
}
