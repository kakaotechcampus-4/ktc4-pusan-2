import type { MixCategory, SpeechMix, SpeechMixBrief } from '@/types/report';
import {
  type ReasonScope,
  mixHidden,
  mixLabel,
  mixMerged,
  percentText,
  reasonText,
} from './contentFormat';

/** 말의 구성 막대 — Take 카드와 장별 펼침이 같이 씁니다 (#1154 3절 8번). 색만으로 나누지 않게 글자 범례를 둡니다 */
const MIX_COLOR: Record<MixCategory, string> = {
  script: 'bg-ink',
  other_slide: 'bg-stone',
  procedural: 'bg-greige',
  off_script: 'bg-coral-deep',
  related_aside: 'bg-coral',
  unrelated: 'bg-line-strong',
  undetermined: 'bg-cream',
};

function mixColor(category: string): string {
  return Object.hasOwn(MIX_COLOR, category) ? MIX_COLOR[category as MixCategory] : 'bg-line';
}

/** 장 요약의 말의 구성(`SpeechMixBrief`)도 같은 모양으로 그립니다 */
export function SpeechMixBlock({
  mix,
  title,
  scope,
}: {
  mix: SpeechMix | SpeechMixBrief | null;
  title: string;
  /** Take 전체인지 장 하나인지 — 이유 문구가 달라집니다 */
  scope: ReasonScope;
}) {
  if (!mix) return null;
  if (mixHidden(mix.state))
    return (
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">{title}</h3>
        <p className="text-sm text-ink/80">{reasonText(mix.reason_codes[0] ?? null, scope)}</p>
      </section>
    );
  if (mix.display.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-bold">{title}</h3>
      <div className="flex h-3 border border-ink bg-cream" aria-hidden="true">
        {mix.display.map((item) => (
          <div
            key={item.category}
            className={mixColor(item.category)}
            style={{ width: `${item.share * 100}%` }}
          />
        ))}
      </div>
      <dl className="flex flex-wrap gap-x-4 gap-y-1">
        {mix.display.map((item) => (
          <div key={item.category} className="flex items-center gap-1.5 text-sm">
            <dt className="flex items-center gap-1.5 text-ink/80">
              <span
                className={`h-2.5 w-2.5 border border-ink/40 ${mixColor(item.category)}`}
                aria-hidden="true"
              />
              {mixLabel(item.category)}
            </dt>
            <dd className="tabular-nums font-bold">{percentText(item.share)}</dd>
          </div>
        ))}
      </dl>
      {mixMerged(mix.reason_codes) && (
        <p className="text-xs text-ink/80">{reasonText('NOT_VALIDATED')}</p>
      )}
    </section>
  );
}
