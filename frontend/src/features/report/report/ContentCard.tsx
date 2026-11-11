import { useId, useState } from 'react';
import { Badge } from '@/shared/ui/Badge';
import { Card } from '@/shared/ui/Panel';
import type {
  ContentStatus,
  CoverageStatus,
  CoverageSummary,
  Expression,
  FactSummary,
  SlideSummary,
  StatusShares,
  TakeCriticalGap,
} from '@/types/report';
import { ContentBroken } from './ContentErrorBoundary';
import { readCoverageSummary } from './contentGuard';
import { SlideCoverage } from './SlideCoverage';
import { SpeechMixBlock } from './SpeechMixBlock';
import {
  COVERAGE_STATUS_LABEL,
  bandText,
  contentHeadline,
  coverageStatusLabel,
  estimatedOnlyLine,
  factsLine,
  hasNoScoredSlides,
  metricReason,
  percentText,
  previousDelta,
  reasonText,
  reasonTexts,
  scopeLine,
  signed,
  slideNotes,
  zeroText,
} from './contentFormat';

/**
 * 내용 전달 카드 — 리포트의 대본 전달도 상세 (#1154 3절). '측정값' 다음에 둡니다.
 *
 * 비율은 null 을 0% 로 바꾸지 않습니다. 값이 없으면 "—" 와 이유를 보이고, 간이 · 추정 값은 배지로 구분합니다.
 * 막대는 색만으로 나누지 않게 `aria-hidden` 막대 + 글자 범례(`<dl>`)로 그립니다 (`GazeBreakdown` 과 같은 방식).
 */
export function ContentCard({
  takeId,
  status: given,
}: {
  takeId: string;
  status: ContentStatus | null;
}) {
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
  // 요약이 객체여도 칸이 비었으면 그리지 않습니다 — 카드만 낮추고 리포트의 다른 칸은 그대로 둡니다 (#1157 리뷰 P1)
  const summary = readCoverageSummary(status.summary);
  if (!summary) return <ContentBroken />;
  return <MeasuredCard takeId={takeId} status={status} summary={summary} />;
}

function MeasuredCard({
  takeId,
  status,
  summary,
}: {
  takeId: string;
  status: ContentStatus;
  summary: CoverageSummary;
}) {
  return (
    <Card title="내용 전달">
      <Headline status={status} summary={summary} />
      {summary.status_shares && <SharesBar shares={summary.status_shares} />}
      <CriticalGaps gaps={summary.critical_gaps} />
      {hasNoScoredSlides(summary) ? (
        // 채점된 장이 없으면 AI 가 개수를 0 으로 채워 보냅니다 — "0개였다"로 읽히지 않게 집계 못 함을 알립니다 (#1157 리뷰)
        <p className="text-sm text-ink/80">
          채점된 장이 없어 핵심 수치 · 표현 · 말의 구성은 집계하지 못했어요.
          {summary.scope.estimated_slides > 0 && ' 장별 간이 결과를 확인해 주세요.'}
        </p>
      ) : (
        // 세 덩어리를 넓은 화면에서는 한 줄에 — 장 수와 상관없이 카드 높이를 비슷하게 둡니다
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Facts facts={summary.facts} />
          <ExpressionLine expression={summary.expression} />
          <SpeechMixBlock mix={summary.speech_mix} title="말의 구성" scope="take" />
        </div>
      )}
      <SlideList takeId={takeId} revision={status.revision} slides={summary.slides} />
    </Card>
  );
}

/* ── 대표 숫자 ─────────────────────────────────────────────────── */

function Headline({ status, summary }: { status: ContentStatus; summary: CoverageSummary }) {
  const delivery = summary.delivery;
  const excluded = delivery.reason_codes.includes('SLIDES_EXCLUDED');
  // 값이 없으면 숫자 자리에 이미 이유가 있어 한 번만 보입니다
  const notes =
    delivery.value === null ? [] : reasonTexts(delivery.reason_codes, ['SLIDES_EXCLUDED'], 'take');
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
              reason={metricReason(summary.contradiction, 'take')}
              provisional={summary.contradiction.state === 'provisional'}
            />
          </dd>
        </div>
      </dl>
      {excluded && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink/80">
          {/* 채점된 장이 하나도 없으면(모두 STT 끊김 · 간이 장만) "일부"가 아니라서 그렇게 말합니다 */}
          <Badge tone="muted">
            {hasNoScoredSlides(summary) ? '평가한 장 없음' : '일부 장 제외'}
          </Badge>
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

/** 채점된 장이 없고 간이 장이 있으면 숫자 대신 간이 단계를 모아 보입니다. 간이 장도 없으면 대표 값의 이유를 보입니다 */
function DeliveryValue({ summary }: { summary: CoverageSummary }) {
  const estimated = hasNoScoredSlides(summary) ? estimatedOnlyLine(summary.slides) : null;
  if (estimated)
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-base font-bold">{estimated}</span>
        <Badge tone="muted">간이</Badge>
      </span>
    );
  return (
    <MetricValue
      value={summary.delivery.value}
      reason={metricReason(summary.delivery, 'take')}
      provisional={summary.delivery.state === 'provisional'}
    />
  );
}

/**
 * 숫자 또는 "—" + 이유. 0 과 null 을 섞지 않습니다.
 * 잠정 값(일부 장 제외 · 음성 인식 끊김 · 문장 단위 평가)은 확정 값과 구별되게 '잠정' 배지를 붙입니다 (#1157 리뷰)
 */
function MetricValue({
  value,
  reason,
  provisional = false,
}: {
  value: number | null;
  reason: string;
  provisional?: boolean;
}) {
  if (value === null)
    return (
      <span className="flex flex-col gap-1">
        <span className="text-3xl font-bold">—</span>
        <span className="text-xs text-ink/80">{reason}</span>
      </span>
    );
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="tabular-nums text-3xl font-bold sm:text-4xl">{percentText(value)}</span>
      {provisional && <Badge tone="muted">잠정</Badge>}
    </span>
  );
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

/* ── 장별 목록 ─────────────────────────────────────────────────── */

function SlideList({
  takeId,
  revision,
  slides,
}: {
  takeId: string;
  revision: number | null;
  slides: SlideSummary[];
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (slides.length === 0) return null;
  // 장이 많으면 카드가 길어져 리포트 아래 카드까지 밀립니다 — 목록은 접어 두고 열면 보입니다
  return (
    <section className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-11 w-full items-center justify-between gap-3 border-2 border-ink bg-cream px-3 text-left text-sm font-bold hover:bg-panel focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        <span>
          장별 보기 <span className="tabular-nums font-normal text-stone">({slides.length}장)</span>
        </span>
        <span aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <ul id={listId} className="flex flex-col border-t border-line">
          {slides.map((slide) => (
            <SlideRow key={slide.slide_number} takeId={takeId} revision={revision} slide={slide} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** 줄을 누르면 그 장의 상세를 펼칩니다. 판(`revision`)을 모르면 부를 수 없어 펼치지 않습니다 */
function SlideRow({
  takeId,
  revision,
  slide,
}: {
  takeId: string;
  revision: number | null;
  slide: SlideSummary;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const head = (
    <>
      <span className="tabular-nums w-10 shrink-0 font-bold">{slide.slide_number}장</span>
      <span className="min-w-0 flex-1">
        <SlideRowBody slide={slide} />
      </span>
    </>
  );
  if (revision === null)
    return (
      <li className="flex min-h-11 items-start gap-3 border-b border-line py-2 text-sm">{head}</li>
    );
  return (
    <li className="border-b border-line text-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-11 w-full items-start gap-3 py-2 text-left hover:bg-cream focus-visible:outline-2 focus-visible:outline-ink"
      >
        {head}
        <span aria-hidden="true" className="shrink-0 text-stone">
          {open ? '▴' : '▾'}
        </span>
      </button>
      {open && (
        <div id={panelId} className="border-t border-line bg-cream/40 px-1 py-3">
          <SlideCoverage takeId={takeId} slideNumber={slide.slide_number} revision={revision} />
        </div>
      )}
    </li>
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
      return (
        <span className="text-ink/80">
          {reasonText(slide.reason_codes[0] ?? null, 'failedSlide')}
        </span>
      );
  }
}

function ScoredRow({ slide }: { slide: SlideSummary }) {
  const delivery = slide.delivery?.value ?? null;
  const deliveryReason = delivery === null ? metricReason(slide.delivery) : null;
  // 채점은 됐지만 음성 인식이 끊겼거나 문장 단위로 평가한 장의 한계 (#1157 리뷰). 위에 보인 이유는 다시 쓰지 않습니다
  const notes = slideNotes(
    slide.reason_codes,
    slide.rubric_quality,
    deliveryReason === null ? [] : [deliveryReason],
  );
  // 잠정 배지는 이유 문구가 아니라 지표 상태로 정합니다 — 이유가 있어도 복구 기준을 채우면 ok 입니다 (#1152, #1157 리뷰)
  const provisional =
    slide.delivery?.state === 'provisional' || slide.contradiction?.state === 'provisional';
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {deliveryReason !== null ? (
        <span className="text-ink/80">{deliveryReason}</span>
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
      {provisional && <Badge tone="muted">잠정</Badge>}
      {notes.length > 0 && (
        <span className="basis-full text-xs text-ink/80">{notes.join(' · ')}</span>
      )}
    </span>
  );
}
