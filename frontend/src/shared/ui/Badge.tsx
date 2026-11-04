import type { ReactNode } from 'react';

export type Tone = 'bad' | 'good' | 'strong' | 'plain' | 'muted';

const TONE: Record<Tone, string> = {
  bad: 'bg-coral-deep text-white',
  good: 'bg-coral-wash text-ink',
  strong: 'bg-ink text-panel',
  plain: 'bg-cream text-ink',
  muted: 'border border-dashed border-line-strong text-stone',
};

/** @param className 크기 · 정렬 클래스. 기본은 글자에 맞춘 폭(`w-fit`)입니다. 색은 tone 만 정합니다 */
export function Badge({
  tone,
  className = 'w-fit',
  children,
}: {
  tone: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={`${className} shrink-0 px-2 py-0.5 text-xs font-bold ${TONE[tone]}`}>
      {children}
    </span>
  );
}
