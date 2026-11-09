import { useEffect, useId, useRef, useState } from 'react';
import { Navigate, useLocation, useParams } from 'react-router';
import { AppHeader } from '@/shared/ui/AppHeader';
import { useTakeReport } from '@/shared/api/report';
import { readAutoEnd } from '@/shared/lib/autoEnd';
import { formatDuration } from '@/shared/lib/clock';
import { Badge, type Tone } from '@/shared/ui/Badge';
import { ButtonLink } from '@/shared/ui/Button';
import { Card, Stat } from '@/shared/ui/Panel';
import type {
  Dimension,
  DimensionStatus,
  JudgeStatus,
  ReviewResult,
  TakeMeasures,
  TakeReport,
} from '@/types/report';
import { AutoEndAlert } from './AutoEndAlert';
import { ContentCard } from './ContentCard';
import { QueryState, ReportFrame } from './ReportFrame';
import {
  DIMENSION_LABEL,
  DIMENSION_STATUS_LABEL,
  JUDGE_STATUS_LABEL,
} from '@/shared/lib/metricLabels';
import {
  OP,
  type ComparisonGroup,
  comparisonGroups,
  endedAt,
  fmt,
  metricLabel,
  pct,
  where,
} from './reportFormat';
import { TakeSurvey } from './TakeSurvey';
import { scriptModeLabel } from './takeHistoryFormat';

/**
 * Take 리포트 (P6).
 *
 * 요약과 지난 Take의 변화를 먼저 보여 주고, 측정 근거와 다음 미션을 이어 보여 줍니다.
 * `score` 는 일부러 보여 주지 않습니다 — FE 원칙이 "점수가 아니라 Take 간 변화"라서입니다 (CLAUDE.md).
 * 시선 3색은 리허설 테두리 전용이라 여기서 쓰지 않습니다.
 *
 * 리뷰가 아직이거나 실패해도 측정값은 먼저 보여 줍니다. 분석 중이면 끝날 때까지 주기적으로 다시 묻습니다.
 */
export function ReportPage() {
  const { takeId = '' } = useParams();
  return <ReportView key={takeId} takeId={takeId} />;
}

function ReportView({ takeId }: { takeId: string }) {
  const report = useTakeReport(takeId);
  // 서버가 자동으로 끝낸 Take 를 막 끝내고 왔으면 분석 대기 화면이 넘긴 이유를 한 번 더 알립니다 (#925)
  const autoEnd = readAutoEnd(useLocation().state);
  const data = report.data;
  if (!takeId) return <Navigate replace to="/" />;
  // 분석 자체가 실패한 Take 는 보여 줄 측정값이 없습니다. 실패 · 리뷰 보류 안내는 분석 대기 화면이 맡습니다
  if (data?.status === 'FAILED' || data?.analysis_hold_reason)
    return <Navigate replace to={`/takes/${takeId}/processing`} />;

  return (
    <div className="app-scroll-shell flex min-h-dvh flex-col">
      <AppHeader />
      <ReportFrame title="발표 리포트" report={data} meta={endedAt(data?.ended_at ?? null)}>
        {autoEnd && <AutoEndAlert notice={autoEnd} tone="page" />}
        <QueryState isPending={report.isPending} error={report.error} />
        {data && <ReportBody key={takeId} report={data} />}
      </ReportFrame>
    </div>
  );
}

function ReportBody({ report }: { report: TakeReport }) {
  const review = report.review;
  const nextTake = report.take_number + 1;

  return (
    <>
      <Hero report={report} />
      {review && <Comparison rows={review.comparison} />}
      {report.measures ? (
        <Measures m={report.measures} />
      ) : (
        <Card title="측정값">
          <p className="text-sm text-ink/80">발표 기록을 정리하고 있어요.</p>
        </Card>
      )}
      <ContentCard status={report.content_status} />

      {/* 측정값이 아직 없으면 평가기준 카드 하나가 줄을 다 씁니다 */}
      <div className="grid gap-4 lg:auto-cols-fr lg:grid-flow-col">
        {report.measures && <GazeBreakdown m={report.measures} />}
        {review ? (
          <JudgeList
            title="내가 정한 평가기준"
            empty="숫자로 확인할 수 있는 평가기준이 없어요."
            rows={review.criteria_results.map((r) => ({
              ...r,
              title: r.display,
              sub: `목표 ${fmt(r.metric, r.target)} ${OP[r.operator]}`,
            }))}
          />
        ) : (
          <Card title="평가기준">
            <p className="text-sm text-ink/80">
              {report.review_status === null
                ? '분석이 끝나면 평가기준 결과를 보여 드릴게요.'
                : '이번 Take의 평가기준 결과를 불러오지 못했어요.'}
            </p>
          </Card>
        )}
      </div>

      {review && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Points
              title="먼저 고칠 것"
              empty="이번 Take에는 크게 고칠 곳이 없어요."
              items={review.improvements}
            />
            <Points
              title="잘한 점"
              empty="아직 강점으로 꼽을 만한 곳이 없어요."
              items={review.strengths}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <JudgeList
              title="이번 Take 미션 결과"
              empty="이번 Take에는 미션이 없었어요."
              rows={review.mission_results.map((r) => ({
                ...r,
                title: `${metricLabel(r.metric)} ${fmt(r.metric, r.target)} ${OP[r.operator]}`,
                sub: r.slide_number === null ? '발표 전체' : `${r.slide_number}번 장`,
              }))}
            />
            <Dimensions value={review.dimensions} />
          </div>

          <NextMissions rows={review.next_missions} nextTake={nextTake} pitchId={report.pitch_id} />
        </>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line-strong pt-5">
        <TakeSurvey takeId={report.take_id} completed={report.status === 'COMPLETED'} />
        <ButtonLink to="/" className="ml-auto">
          내 피치로
        </ButtonLink>
      </footer>
    </>
  );
}

/* ── 한 줄 요약 ─────────────────────────────────────────────────── */

/** 리뷰가 없을 때의 한 줄 */
function noReviewLine(status: TakeReport['review_status']) {
  switch (status) {
    case null:
      return '발표를 분석하고 있어요. 측정값은 먼저 보여 드릴게요.';
    case 'SKIPPED':
      return '이번 Take는 총평 없이 측정값만 보여 드려요.';
    default:
      return '이번 Take는 총평을 만들지 못했어요. 측정값은 그대로 확인할 수 있어요.';
  }
}

function Hero({ report }: { report: TakeReport }) {
  const review = report.review;

  return (
    <section className="relative flex flex-row-reverse items-center justify-between gap-5 border-2 border-ink bg-panel p-6 shadow-[4px_4px_0_var(--color-line)] sm:p-8">
      {/* 오른쪽 끝에 서므로 좌우를 뒤집어 문장 쪽을 보게 합니다 */}
      <img
        src="/onboarding/pico-bust.png"
        alt=""
        width={80}
        height={80}
        className="hidden h-28 w-28 shrink-0 -scale-x-100 object-contain [image-rendering:pixelated] sm:block"
      />
      <div className="min-w-0">
        <p className="text-xs font-bold text-ink/80">피코의 코칭 노트</p>
        <p role="status" className="mt-3 max-w-3xl text-xl font-bold leading-relaxed xl:text-3xl">
          {review?.overall_summary ?? noReviewLine(report.review_status)}
        </p>
        {review?.fallback_reason && (
          <p className="mt-2 text-sm text-ink/80">
            코치 문장을 만들지 못해 기본 문장으로 보여 드려요. 숫자와 판정은 같아요.
          </p>
        )}
      </div>
    </section>
  );
}

/* ── 측정값 (리뷰가 없어도 보임) ────────────────────────────────── */

function Measures({ m }: { m: TakeMeasures }) {
  const over = m.duration_ms - m.goal_ms;
  return (
    <section aria-label="측정값" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="발표 시간" value={formatDuration(m.duration_ms)}>
        목표 {formatDuration(m.goal_ms)} ·{' '}
        {over <= 0 ? `${formatDuration(-over)} 남김` : `${formatDuration(over)} 넘김`}
      </Stat>
      <Stat label="청중 응시" value={pct(m.audience_ratio)}>
        대본 응시 {pct(m.script_ratio)}
      </Stat>
      <Stat label="말하기 속도" value={m.cpm === null ? '—' : `${m.cpm}자/분`}>
        Take 평균
      </Stat>
      <Stat label="군더더기" value={m.filler_count === null ? '—' : `${m.filler_count}회`}>
        이번 Take 합계
      </Stat>
    </section>
  );
}

/**
 * 시선 네 곳의 비율 (`gaze-tracking.md` 5절). 분모가 모두 측정한 시간이라 막대 하나에 쌓으면 100%입니다.
 * 시선 3색은 리허설 테두리 전용이라 기본 색으로 나누고, 색만으로 구분하지 않게 숫자를 같이 둡니다.
 */
function GazeBreakdown({ m }: { m: TakeMeasures }) {
  const rows = [
    { label: '청중', value: m.audience_ratio, color: 'bg-coral-deep' },
    { label: '화면', value: m.screen_ratio, color: 'bg-stone' },
    { label: '대본', value: m.script_ratio, color: 'bg-ink' },
    { label: '다른 곳', value: m.away_ratio, color: 'bg-line-strong' },
  ];

  if (rows.every((row) => row.value === null))
    return (
      <Card title="시선 비율">
        <p className="text-sm text-ink/80">
          이번 Take는 시선을 재지 않았어요. 말하기 · 시간 기록은 그대로예요.
        </p>
      </Card>
    );

  return (
    <Card title="시선 비율">
      <div className="flex h-4 border border-ink bg-cream" aria-hidden="true">
        {rows.map(
          (row) =>
            row.value !== null && (
              <div key={row.label} className={row.color} style={{ width: `${row.value * 100}%` }} />
            ),
        )}
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {rows.map((row) => (
          <div key={row.label}>
            <dt className="flex items-center gap-2 text-sm text-ink/80">
              <span className={`h-2.5 w-2.5 ${row.color}`} aria-hidden="true" />
              {row.label}
            </dt>
            <dd className="tabular mt-1 tabular-nums text-3xl font-bold">{pct(row.value)}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs leading-relaxed text-ink/80">
        측정한 시간 기준이에요.
        {m.unmeasured_ms !== null &&
          ` 얼굴이 안 보이는 등 측정 못 한 ${Math.round(m.unmeasured_ms / 1000)}초는 빼고 계산했어요.`}
      </p>
    </Card>
  );
}

/* ── 고칠 것 · 잘한 점 ──────────────────────────────────────────── */

function Points({
  title,
  empty,
  items,
}: {
  title: string;
  empty: string;
  items: ReviewResult['strengths'];
}) {
  return (
    <Card title={title}>
      {items.length === 0 ? (
        <p className="text-sm text-ink/80">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {items.map((p, i) => (
            <li key={i} className="border-b border-line px-0 py-4 last:border-b-0">
              <p className="text-xs font-bold text-ink/80">{where(p.area, p.slide_number)}</p>
              <p className="mt-1 font-bold leading-snug">{p.text}</p>
              <p className="tabular mt-1.5 text-xs text-ink/80">
                {Object.entries(p.evidence)
                  .map(([k, v]) => `${metricLabel(k)} ${fmt(k, v)}`)
                  .join(' · ')}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ── 지난 Take와 비교 ───────────────────────────────────────────── */

/** `improved` 는 true · false · null(방향을 정할 수 없음) */
const TREND: Record<string, { label: string; tone: Tone }> = {
  true: { label: '나아짐', tone: 'good' },
  false: { label: '나빠짐', tone: 'bad' },
  null: { label: '방향 판단 불가', tone: 'muted' },
  same: { label: '같음', tone: 'muted' },
};

/** 화면에 보이는 값이 같으면 AI 의 `improved`(같으면 false)와 상관없이 "같음" */
function trend(r: ReviewResult['comparison'][number]) {
  return fmt(r.metric, r.previous) === fmt(r.metric, r.current)
    ? TREND.same!
    : TREND[String(r.improved)]!;
}

/** 지난 Take의 지표별 슬라이드 변화 요약과 차원별 상세 비교표 (#929). */
function slideList(numbers: number[]) {
  const unique = [...new Set(numbers)].sort((a, b) => a - b);
  return unique.length ? unique.map((n) => `${n}번`).join(', ') : '없음';
}

/** 항상 발표 전체 기준으로만 산출되는 지표. 장별 값이 빠진 다른 지표와 구분합니다. */
const OVERALL_ONLY_METRICS = new Set(['duration_ms', 'content_coverage', 'critical_fact_accuracy']);

function Comparison({ rows }: { rows: ReviewResult['comparison'] }) {
  const groups = comparisonGroups(rows);
  // AI가 대본 보기 차이를 모든 행에 싣더라도 안내는 한 번만 보여 줍니다.
  const mode = rows.find((r) => r.script_mode)?.script_mode;
  const [open, setOpen] = useState(false);
  const detailTitleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  return (
    <Card title="지난 Take와 비교">
      {groups.length === 0 ? (
        <p className="text-sm text-ink/80">
          {rows.length === 0 ? '비교할 지난 Take가 없어요.' : '지난 Take와 함께 잰 지표가 없어요.'}
        </p>
      ) : (
        <>
          {mode && (
            <p className="text-sm text-ink/80">
              대본 보기가 달랐어요 ({scriptModeLabel(mode.previous)} →{' '}
              {scriptModeLabel(mode.current)}). 비교는 참고만 해 주세요.
            </p>
          )}
          <p className="text-sm text-ink/80">
            지표별로 지난 연습과의 차이와 비교 가능한 슬라이드를 정리했어요.
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            {groups
              .flatMap((g) => g.metrics)
              .map((metric) => {
                const metricRows = rows.filter((r) => r.metric === metric);
                const slides = metricRows.filter((r) => r.slide_number !== null);
                const improved = slides
                  .filter((r) => trend(r) === TREND.true)
                  .map((r) => r.slide_number!);
                const worsened = slides
                  .filter((r) => trend(r) === TREND.false)
                  .map((r) => r.slide_number!);
                const same = slides.filter((r) => trend(r) === TREND.same).length;
                const unknown = slides.filter((r) => trend(r) === TREND.null).length;
                const overall = metricRows.find((r) => r.slide_number === null);
                const directionUnknown =
                  slides.length > 0 && unknown === slides.length && same === 0;
                return (
                  <section key={metric} className="min-w-0 border-l-2 border-line pl-3">
                    <h3 className="font-bold">{metricLabel(metric)}</h3>
                    {slides.length > 0 && directionUnknown && (
                      <p className="mt-2 text-sm text-ink/80">
                        개선·악화 방향을 판단할 수 없는 지표예요. 해당 슬라이드 {unknown}개
                      </p>
                    )}
                    {slides.length > 0 && !directionUnknown && (
                      <>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                          <div className="border border-line-strong bg-panel p-3">
                            <p className="font-bold text-ink">나아진 슬라이드</p>
                            <p className="mt-1">{slideList(improved)}</p>
                          </div>
                          <div className="border border-line-strong bg-panel p-3">
                            <p className="font-bold text-ink">나빠진 슬라이드</p>
                            <p className="mt-1">{slideList(worsened)}</p>
                          </div>
                        </div>
                        {(same > 0 || unknown > 0) && (
                          <p className="mt-2 text-xs text-ink/80">
                            {same > 0 && `변화 없음 ${same}개`}
                            {same > 0 && unknown > 0 && ' · '}
                            {unknown > 0 && `방향 판단 불가 ${unknown}개`}
                          </p>
                        )}
                      </>
                    )}
                    {slides.length === 0 && (
                      <p className="mt-2 text-sm text-ink/80">
                        {overall && OVERALL_ONLY_METRICS.has(metric)
                          ? '발표 전체 기준 지표예요.'
                          : '슬라이드별 비교 결과가 없어요.'}
                      </p>
                    )}
                    {overall && (
                      <p className="mt-2 text-xs text-ink/80">
                        발표 전체: {fmt(metric, overall.previous)} → {fmt(metric, overall.current)}{' '}
                        · {trend(overall).label}
                      </p>
                    )}
                  </section>
                );
              })}
          </div>
          <button
            ref={openerRef}
            type="button"
            onClick={() => setOpen(true)}
            className="min-h-11 w-fit border-2 border-ink bg-panel px-4 py-2 text-sm font-bold hover:bg-cream focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-deep"
          >
            상세 비교표 보기
          </button>
          <dialog
            ref={dialogRef}
            aria-labelledby={detailTitleId}
            onClose={() => {
              setOpen(false);
              openerRef.current?.focus();
            }}
            className="fixed inset-0 m-auto max-h-[90dvh] w-[min(96vw,72rem)] max-w-none overflow-y-auto border-2 border-ink bg-panel p-4 text-ink shadow-[6px_6px_0_var(--color-line)] backdrop:bg-ink/60 sm:p-6"
          >
            <div className="mb-5 flex items-start justify-between gap-4">
              <div>
                <h2 id={detailTitleId} className="text-lg font-bold">
                  지난 Take 상세 비교
                </h2>
                <p className="mt-1 text-xs text-ink/80">칸마다 지난 Take → 이번 Take</p>
              </div>
              <button
                type="button"
                onClick={() => dialogRef.current?.close()}
                className="min-h-11 shrink-0 border-2 border-ink px-4 font-bold focus-visible:outline-2 focus-visible:outline-coral-deep"
                aria-label="상세 비교 닫기"
              >
                닫기
              </button>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              {groups.map((g) => (
                <ComparisonTable key={g.dimension ?? 'other'} group={g} />
              ))}
            </div>
          </dialog>
        </>
      )}
    </Card>
  );
}

function ComparisonTable({ group }: { group: ComparisonGroup }) {
  const title = group.dimension === null ? '그 밖의 지표' : DIMENSION_LABEL[group.dimension];
  return (
    // 열이 셋 이상이면 한 줄을 다 씁니다
    <section
      aria-label={title}
      className={`min-w-0 border-l-2 border-line pl-3 ${group.metrics.length > 2 ? 'lg:col-span-2' : ''}`}
    >
      <h3 className="text-sm font-bold">{title}</h3>
      <div className="overflow-x-auto">
        <table className="tabular mt-2 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-ink/80">
              <th scope="col" className="w-20 py-1.5 pr-4 font-normal">
                구간
              </th>
              {group.metrics.map((m) => (
                <th key={m} scope="col" className="py-1.5 pr-4 font-bold text-ink">
                  {metricLabel(m)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {group.slides.map((slide) => (
              <tr key={slide ?? 'take'} className="border-b border-line last:border-b-0">
                <th scope="row" className="whitespace-nowrap py-2 pr-4 text-xs font-normal">
                  {slide === null ? '발표 전체' : `${slide}번 장`}
                </th>
                {group.metrics.map((m) => {
                  const r = group.cell(m, slide);
                  return (
                    <td key={m} className="py-2 pr-4">
                      {r ? (
                        <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                          <span className="text-ink/80">{fmt(r.metric, r.previous)}</span>→
                          <b className="text-base">{fmt(r.metric, r.current)}</b>
                          <Badge tone={trend(r).tone}>{trend(r).label}</Badge>
                        </span>
                      ) : (
                        <span className="text-ink/80">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ── 영역별 상태 ────────────────────────────────────────────────── */

const DIMENSION_STATUS: Record<DimensionStatus, { label: string; tone: Tone }> = {
  PRIORITY: { label: DIMENSION_STATUS_LABEL.PRIORITY, tone: 'bad' },
  IMPROVED: { label: DIMENSION_STATUS_LABEL.IMPROVED, tone: 'good' },
  STRENGTH: { label: DIMENSION_STATUS_LABEL.STRENGTH, tone: 'strong' },
  STABLE: { label: DIMENSION_STATUS_LABEL.STABLE, tone: 'plain' },
  NOT_EVALUABLE: { label: DIMENSION_STATUS_LABEL.NOT_EVALUABLE, tone: 'muted' },
};

function Dimensions({ value }: { value: ReviewResult['dimensions'] }) {
  return (
    <Card title="영역별 상태">
      <ul className="grid grid-cols-2 gap-2">
        {(Object.keys(DIMENSION_LABEL) as Dimension[]).map((d) => {
          const status = value[d];
          const s = Object.hasOwn(DIMENSION_STATUS, status)
            ? DIMENSION_STATUS[status as DimensionStatus]
            : DIMENSION_STATUS.NOT_EVALUABLE;
          return (
            <li key={d} className="flex flex-col gap-2 border-b border-line px-0 py-3">
              <span className="text-sm font-bold">{DIMENSION_LABEL[d]}</span>
              <Badge tone={s.tone}>{s.label}</Badge>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* ── 미션 결과 · 평가기준 ───────────────────────────────────────── */

const JUDGE: Record<JudgeStatus, { label: string; tone: Tone }> = {
  ACHIEVED: { label: JUDGE_STATUS_LABEL.ACHIEVED, tone: 'strong' },
  PARTIAL: { label: JUDGE_STATUS_LABEL.PARTIAL, tone: 'good' },
  FAILED: { label: JUDGE_STATUS_LABEL.FAILED, tone: 'bad' },
  NOT_EVALUABLE: { label: JUDGE_STATUS_LABEL.NOT_EVALUABLE, tone: 'muted' },
};

/** 미션 결과 · 사용자 평가기준 — 판정 모양이 같아 한 목록으로 그립니다 */
function JudgeList({
  title,
  empty,
  rows,
}: {
  title: string;
  empty: string;
  rows: {
    title: string;
    sub: string;
    metric: string;
    observed: number | null;
    status: JudgeStatus;
  }[];
}) {
  return (
    <Card title={title}>
      {rows.length === 0 ? (
        <p className="text-sm text-ink/80">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {rows.map((r, i) => (
            <li
              key={i}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-0 py-4 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="font-bold">{r.title}</p>
                <p className="mt-0.5 text-xs text-ink/80">{r.sub}</p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="tabular text-sm">
                  이번 <b>{r.observed === null ? '—' : fmt(r.metric, r.observed)}</b>
                </span>
                <Badge tone={JUDGE[r.status].tone}>{JUDGE[r.status].label}</Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ── 다음 Take 미션 ─────────────────────────────────────────────── */

function NextMissions({
  rows,
  nextTake,
  pitchId,
}: {
  rows: ReviewResult['next_missions'];
  nextTake: number;
  pitchId: string;
}) {
  return (
    <section className="border-2 border-ink bg-coral-wash p-6 shadow-[4px_4px_0_var(--color-line)] xl:p-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Take {nextTake}에서 해 볼 것</h2>
          <p className="mt-1 text-sm text-ink">
            다음 연습에서 코치가 이 미션을 기준으로 알려 드려요.
          </p>
        </div>
        {/* 이동만 합니다. Take는 시작 전 세팅에서 생깁니다 (CLAUDE.md 8번) */}
        <ButtonLink to={`/pitch/${pitchId}/device-check`}>Take {nextTake} 연습하기</ButtonLink>
      </div>
      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-ink">이번에는 새 미션이 없어요. 지금처럼 연습해 보세요.</p>
      ) : (
        <ol className="mt-4 flex flex-col gap-2.5">
          {rows.map((r) => (
            <li key={r.priority} className="flex gap-4 border-t border-ink/15 px-0 py-4">
              <span className="tabular flex h-7 w-7 shrink-0 items-center justify-center bg-coral-deep text-sm font-bold text-white">
                {r.priority}
              </span>
              <div className="min-w-0">
                <p className="font-bold">{r.description}</p>
                <p className="mt-0.5 text-sm text-ink/80">
                  {where(r.area, r.slide_number)}
                  {r.reason ? ` · ${r.reason}` : ''}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
