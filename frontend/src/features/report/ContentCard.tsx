import { useState } from 'react';
import { Badge } from '@/shared/ui/Badge';
import { Card } from '@/shared/ui/Panel';
import type {
  ContentStatus,
  CoverageStatus,
  CoverageSummary,
  Expression,
  FactSummary,
  MixCategory,
  SlideSummary,
  SpeechMix,
  SpeechMixBrief,
  StatusShares,
  TakeCriticalGap,
} from '@/types/report';
import {
  COVERAGE_STATUS_LABEL,
  bandText,
  contentHeadline,
  coverageStatusLabel,
  estimatedOnlyLine,
  factsLine,
  isEstimatedOnly,
  metricReason,
  mixHidden,
  mixLabel,
  mixMerged,
  percentText,
  previousDelta,
  reasonText,
  reasonTexts,
  scopeLine,
  signed,
  zeroText,
} from './contentFormat';

/**
 * 내용 전달 카드 — 리포트의 대본 전달도 상세 (#1154 3절). '측정값' 다음에 둡니다.
 *
 * 비율은 null 을 0% 로 바꾸지 않습니다. 값이 없으면 "—" 와 이유를 보이고, 간이 · 추정 값은 배지로 구분합니다.
 * 막대는 색만으로 나누지 않게 `aria-hidden` 막대 + 글자 범례(`<dl>`)로 그립니다 (`GazeBreakdown` 과 같은 방식).
 */
export function ContentCard({ status: given }: { status: ContentStatus | null }) {
  // 요청 함수가 null 로 맞추지만, 키가 없는 캐시 · 목이 와도 깨지지 않게 한 번 더 맞춥니다
  const status = given ?? null;
  const headline = contentHeadline(status);
  if (headline !== null || !status?.summary)
    return (
      <Card title="내용 전달">
        <p className="text-sm text-ink/80">{headline ?? reasonText('NO_RESULT')}</p>
        {status?.state === 'FAILED' && (
          <p className="text-xs text-ink/80">다시 분석은 아직 지원하지 않아요.</p>
        )}
      </Card>
    );
  return <MeasuredCard status={status} summary={status.summary} />;
}

function MeasuredCard({ status, summary }: { status: ContentStatus; summary: CoverageSummary }) {
  return (
    <Card title="내용 전달">
      <Headline status={status} summary={summary} />
      {summary.status_shares && <SharesBar shares={summary.status_shares} />}
      <CriticalGaps gaps={summary.critical_gaps} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Facts facts={summary.facts} />
        <ExpressionLine expression={summary.expression} />
      </div>
      <SpeechMixBlock mix={summary.speech_mix} title="말의 구성" />
      <SlideList slides={summary.slides} />
    </Card>
  );
}

/* ── 대표 숫자 ─────────────────────────────────────────────────── */

function Headline({ status, summary }: { status: ContentStatus; summary: CoverageSummary }) {
  const delivery = summary.delivery;
  const excluded = delivery.reason_codes.includes('SLIDES_EXCLUDED');
  // 값이 없으면 숫자 자리에 이미 이유가 있어 한 번만 보입니다
  const notes =
    delivery.value === null ? [] : reasonTexts(delivery.reason_codes, ['SLIDES_EXCLUDED']);
  const zero = zeroText(delivery, summary.zero_kind);
  const prev = previousDelta(status);

  return (
    <div className="flex flex-col gap-3">
      <dl className="grid gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-sm font-bold text-stone">핵심 내용 전달률</dt>
          <dd className="mt-2">
            <DeliveryValue summary={summary} />
          </dd>
        </div>
        <div>
          <dt className="text-sm font-bold text-stone">다르게 말함</dt>
          <dd className="mt-2">
            <MetricValue
              value={summary.contradiction.value}
              reason={metricReason(summary.contradiction)}
            />
          </dd>
        </div>
      </dl>
      {excluded && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink/80">
          <Badge tone="muted">일부 장 제외</Badge>
          {scopeLine(summary.scope)}
        </p>
      )}
      {notes.map((note) => (
        <p key={note} className="text-xs text-ink/80">
          {note}
        </p>
      ))}
      {zero && <p className="text-sm font-bold">{zero}</p>}
      {prev && (
        <p className="text-sm">
          지난 Take보다 <span className="tabular-nums font-bold">{signed(prev.delta)}%p</span>
          {prev.partial && (
            <span className="ml-2 text-xs text-ink/80">일부 장이 평가에서 제외됐어요</span>
          )}
        </p>
      )}
    </div>
  );
}

/** 채점된 장이 없고 간이 장만 있으면 숫자 대신 간이 단계를 모아 보입니다 */
function DeliveryValue({ summary }: { summary: CoverageSummary }) {
  const estimated = isEstimatedOnly(summary) ? estimatedOnlyLine(summary.slides) : null;
  if (estimated)
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-base font-bold">{estimated}</span>
        <Badge tone="muted">간이</Badge>
      </span>
    );
  return <MetricValue value={summary.delivery.value} reason={metricReason(summary.delivery)} />;
}

/** 숫자 또는 "—" + 이유. 0 과 null 을 섞지 않습니다 */
function MetricValue({ value, reason }: { value: number | null; reason: string }) {
  if (value === null)
    return (
      <span className="flex flex-col gap-1">
        <span className="text-3xl font-bold">—</span>
        <span className="text-xs text-ink/80">{reason}</span>
      </span>
    );
  return <span className="tabular-nums text-3xl font-bold sm:text-4xl">{percentText(value)}</span>;
}

/* ── 4칸 막대 ──────────────────────────────────────────────────── */

const SHARE_ORDER: CoverageStatus[] = ['covered', 'partial', 'missing', 'contradicted'];
const SHARE_COLOR: Record<CoverageStatus, string> = {
  covered: 'bg-ink',
  partial: 'bg-stone',
  missing: 'bg-line-strong',
  contradicted: 'bg-coral-deep',
};

function SharesBar({ shares }: { shares: StatusShares }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-4 border border-ink bg-cream" aria-hidden="true">
        {SHARE_ORDER.map((key) => (
          <div key={key} className={SHARE_COLOR[key]} style={{ width: `${shares[key] * 100}%` }} />
        ))}
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {SHARE_ORDER.map((key) => (
          <div key={key}>
            <dt className="flex items-center gap-2 text-sm text-ink/80">
              <span className={`h-2.5 w-2.5 ${SHARE_COLOR[key]}`} aria-hidden="true" />
              {COVERAGE_STATUS_LABEL[key]}
            </dt>
            <dd className="tabular-nums mt-1 text-xl font-bold">{percentText(shares[key])}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/* ── 놓친 중요 내용 ────────────────────────────────────────────── */

const GAP_LIMIT = 5;

function CriticalGaps({ gaps }: { gaps: TakeCriticalGap[] }) {
  const [all, setAll] = useState(false);
  if (gaps.length === 0) return null;
  const shown = all ? gaps : gaps.slice(0, GAP_LIMIT);
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-bold">놓친 중요 내용</h3>
      <ul className="flex flex-col gap-1.5">
        {shown.map((gap) => (
          <li key={`${gap.slide_number}-${gap.key_point_id}`} className="text-sm leading-relaxed">
            <span className="tabular-nums font-bold">{gap.slide_number}장</span> · {gap.content} ·{' '}
            <span className="font-bold">{coverageStatusLabel(gap.status)}</span>
            {gap.said_in_slides.length > 0 && (
              <span className="text-ink/80"> ({gap.said_in_slides.join(', ')}장에서 말함)</span>
            )}
          </li>
        ))}
      </ul>
      {gaps.length > GAP_LIMIT && (
        <button
          type="button"
          aria-expanded={all}
          onClick={() => setAll((v) => !v)}
          className="min-h-11 w-fit text-sm font-bold underline underline-offset-4"
        >
          {all ? '접기' : `${gaps.length - GAP_LIMIT}개 더 보기`}
        </button>
      )}
    </section>
  );
}

/* ── 수치 · 표현 ───────────────────────────────────────────────── */

function Facts({ facts }: { facts: FactSummary }) {
  const line = factsLine(facts);
  if (line.kind === 'none')
    return (
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">핵심 수치</h3>
        <p className="text-sm text-ink/80">확인할 수치 0개</p>
      </section>
    );
  const chips = [
    { label: '맞음', count: facts.matched },
    { label: '어림', count: facts.approximate },
    { label: '다른 값', count: facts.mismatched },
    { label: '빠뜨림', count: facts.missing },
    { label: '확인 대기', count: facts.pending },
  ];
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-bold">핵심 수치</h3>
      <p className="flex flex-wrap items-center gap-2 text-sm">
        {line.text}
        {line.kind === 'estimate' && <Badge tone="muted">추정</Badge>}
      </p>
      <ul className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <li key={chip.label} className="border border-line-strong px-2 py-0.5 text-xs">
            {chip.label} <span className="tabular-nums font-bold">{chip.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ExpressionLine({ expression }: { expression: Expression }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-bold">표현</h3>
      <p className="text-sm">
        대본대로 <span className="tabular-nums font-bold">{expression.verbatim_like}</span> · 바꿔
        말함 <span className="tabular-nums font-bold">{expression.paraphrased}</span> · 구분 어려움{' '}
        <span className="tabular-nums font-bold">{expression.undetermined}</span>
      </p>
      <p className="text-xs text-ink/80">바꿔 말해도 감점하지 않아요.</p>
    </section>
  );
}

/* ── 말의 구성 ─────────────────────────────────────────────────── */

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
}: {
  mix: SpeechMix | SpeechMixBrief | null;
  title: string;
}) {
  if (!mix) return null;
  if (mixHidden(mix.state))
    return (
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">{title}</h3>
        <p className="text-sm text-ink/80">{reasonText(mix.reason_codes[0] ?? null)}</p>
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

/* ── 장별 목록 ─────────────────────────────────────────────────── */

function SlideList({ slides }: { slides: SlideSummary[] }) {
  if (slides.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-bold">장별</h3>
      <ul className="flex flex-col border-t border-line">
        {slides.map((slide) => (
          <li
            key={slide.slide_number}
            className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line py-2 text-sm"
          >
            <span className="tabular-nums w-10 shrink-0 font-bold">{slide.slide_number}장</span>
            <SlideRowBody slide={slide} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 한 줄 — 채점됨 · 간이 · 실패 (#1154 3절 10번) */
export function SlideRowBody({ slide }: { slide: SlideSummary }) {
  switch (slide.state) {
    case 'scored':
      return <ScoredRow slide={slide} />;
    case 'estimated': {
      const band = bandText(slide.delivery_band);
      return (
        <span className="flex flex-wrap items-center gap-2">
          {band ?? reasonText(slide.reason_codes[0] ?? null)}
          <Badge tone="muted">간이</Badge>
        </span>
      );
    }
    default:
      return <span className="text-ink/80">{reasonText(slide.reason_codes[0] ?? null, true)}</span>;
  }
}

function ScoredRow({ slide }: { slide: SlideSummary }) {
  const delivery = slide.delivery?.value ?? null;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {delivery === null ? (
        <span className="text-ink/80">{metricReason(slide.delivery)}</span>
      ) : (
        <span>
          전달 <span className="tabular-nums font-bold">{percentText(delivery)}</span>
        </span>
      )}
      {slide.contradiction?.value != null && (
        <span>
          다르게 말함{' '}
          <span className="tabular-nums font-bold">{percentText(slide.contradiction.value)}</span>
        </span>
      )}
      {slide.critical_gap_count > 0 && (
        <span>
          놓친 중요 내용 <span className="tabular-nums font-bold">{slide.critical_gap_count}</span>
        </span>
      )}
      {slide.rubric_quality === 'sentence_fallback' && <Badge tone="muted">참고용</Badge>}
    </span>
  );
}
