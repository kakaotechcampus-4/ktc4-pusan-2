import { Navigate, useParams } from 'react-router';
import { AppHeader } from '@/shared/ui/AppHeader';
import { useTakeReport } from '@/shared/api/report';
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
import { QueryState, ReportFrame } from './ReportFrame';
import { OP, endedAt, fmt, metricLabel, pct, where } from './reportFormat';
import { TakeSurvey } from './TakeSurvey';

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
  const data = report.data;
  if (!takeId) return <Navigate replace to="/" />;
  // 분석 자체가 실패한 Take 는 보여 줄 측정값이 없습니다. 실패 · 리뷰 보류 안내는 분석 대기 화면이 맡습니다
  if (data?.status === 'FAILED' || data?.analysis_hold_reason)
    return <Navigate replace to={`/takes/${takeId}/processing`} />;

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader />
      <ReportFrame title="발표 리포트" report={data} meta={endedAt(data?.ended_at ?? null)}>
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
            <dd className="tabular mt-1 font-mono text-3xl font-bold">{pct(row.value)}</dd>
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
  null: { label: '방향 없음', tone: 'muted' },
  same: { label: '같음', tone: 'muted' },
};

/** 화면에 보이는 값이 같으면 AI 의 `improved`(같으면 false)와 상관없이 "같음" */
function trend(r: ReviewResult['comparison'][number]) {
  return fmt(r.metric, r.previous) === fmt(r.metric, r.current)
    ? TREND.same!
    : TREND[String(r.improved)]!;
}

function Comparison({ rows }: { rows: ReviewResult['comparison'] }) {
  return (
    <Card title="지난 Take와 비교">
      {rows.length === 0 ? (
        <p className="text-sm text-ink/80">비교할 지난 Take가 없어요.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {rows.map((r, i) => (
            <li key={i} className="flex min-w-0 flex-col gap-1.5 border-l-2 border-line pl-3">
              <span className="text-xs text-ink/80">
                <b className="text-sm text-ink">{metricLabel(r.metric)}</b>
                {r.slide_number !== null && <> · {r.slide_number}번 장</>}
              </span>
              <span className="tabular flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
                <span className="text-ink/80">{fmt(r.metric, r.previous)}</span>→
                <b className="text-lg">{fmt(r.metric, r.current)}</b>
                <Badge tone={trend(r).tone}>{trend(r).label}</Badge>
              </span>
              {r.script_mode && (
                <p className="text-xs text-ink/80">
                  대본 보기가 달랐어요 ({r.script_mode.previous} → {r.script_mode.current}). 비교는
                  참고만 해 주세요.
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ── 영역별 상태 ────────────────────────────────────────────────── */

const DIMENSION_LABEL: Record<Dimension, string> = {
  audience_gaze: '청중 응시',
  script_independence: '대본에서 벗어나기',
  pacing_rhythm: '속도 · 리듬',
  vocal_delivery: '목소리',
  fluency: '군더더기 없이',
  content_delivery: '내용 전달',
};

const DIMENSION_STATUS: Record<DimensionStatus, { label: string; tone: Tone }> = {
  PRIORITY: { label: '먼저 고칠 것', tone: 'bad' },
  IMPROVED: { label: '나아짐', tone: 'good' },
  STRENGTH: { label: '강점', tone: 'strong' },
  STABLE: { label: '유지', tone: 'plain' },
  NOT_EVALUABLE: { label: '판단 불가', tone: 'muted' },
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
  ACHIEVED: { label: '달성', tone: 'strong' },
  PARTIAL: { label: '거의 달성', tone: 'good' },
  FAILED: { label: '미달성', tone: 'bad' },
  NOT_EVALUABLE: { label: '판단 불가', tone: 'muted' },
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
