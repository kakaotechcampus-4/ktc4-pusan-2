import type { TakeStatusView } from '@/shared/lib/takeStatus';
import type { Tone } from './Badge';

const TONE: Record<Tone, string> = {
  bad: 'border-coral bg-coral-wash text-coral-deep',
  good: 'border-coral bg-coral-wash text-coral-deep',
  strong: 'border-ink bg-ink text-panel',
  plain: 'border-line-strong bg-cream text-ink',
  muted: 'border-dashed border-line-strong bg-panel text-stone',
};

/** 목록의 상태 칸은 문구 길이와 관계없이 같은 크기와 중심을 유지합니다. */
export function TakeStatusBadge({ status }: { status: Pick<TakeStatusView, 'label' | 'tone'> }) {
  return (
    <span
      className={`inline-flex h-7 w-20 shrink-0 items-center justify-center whitespace-nowrap border px-2 text-center text-xs font-bold leading-none ${TONE[status.tone]}`}
    >
      {status.label}
    </span>
  );
}
