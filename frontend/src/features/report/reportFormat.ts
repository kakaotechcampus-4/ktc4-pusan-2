import { formatDuration } from '@/shared/lib/clock';
import type { AnalysisStage, Area, Dimension, Operator, ReviewResult } from '@/types/report';

/* ── 분석 대기 단계 ─────────────────────────────────────────────── */

export type StepState = 'DONE' | 'RUNNING' | 'WAITING' | 'PENDING';

/** 화면의 세 단계 — BE 흐름 그대로입니다 (`docs/ai/architecture.md` 1-3) */
const STEP_STAGE: AnalysisStage[] = ['FINALIZING', 'SCORING', 'REVIEWING'];

/**
 * BE 단계 하나를 화면 세 단계의 상태로 펼칩니다. 단계가 아직 없으면(null) 첫 단계가 도는 중입니다.
 * 대본 확인(`AWAITING_CONFIRMATION`)은 채점 단계에 붙은 기다림입니다.
 */
export function stepStates(stage: AnalysisStage | null): StepState[] {
  if (stage === 'AWAITING_CONFIRMATION') return ['DONE', 'WAITING', 'PENDING'];
  const current = stage === 'DONE' ? STEP_STAGE.length : STEP_STAGE.indexOf(stage ?? 'FINALIZING');
  return STEP_STAGE.map((_, i) => {
    if (i < current) return 'DONE';
    if (i === current) return 'RUNNING';
    return 'PENDING';
  });
}

/* ── 이름 · 숫자 ────────────────────────────────────────────────── */

const AREA: Record<Area, string> = {
  GAZE: '시선',
  SPEED: '말 속도',
  VOLUME: '소리 크기',
  PAUSE: '침묵',
  FILLER: '군더더기',
  CONTENT: '내용 전달',
  TIME: '시간',
};

export const OP: Record<Operator, string> = { LT: '미만', LTE: '이하', GT: '초과', GTE: '이상' };

// 지표 이름 · 값 표기는 대화 에이전트와 함께 써서 shared 로 옮겼습니다
export { fmt, metricLabel } from '@/shared/lib/metricLabels';

/* ── 지난 Take와 비교 ───────────────────────────────────────────── */

/**
 * 리뷰 차원과 그 지표 — `docs/ai/features/review-agent.md` 5-3 차원 표 그대로입니다
 * (AI 코드 `features/review/vocab.py` `DIMENSIONS`). 순서도 그 표의 순서이고, AI 가 비교 행을 내는 순서와 같습니다.
 * FE 가 지표를 차원에 새로 넣지 않습니다 — 표가 바뀌면 여기를 같이 고칩니다.
 */
const DIMENSION_METRICS: Record<Dimension, readonly string[]> = {
  audience_gaze: ['audience_ratio'],
  script_independence: ['script_ratio', 'reading_ratio', 'reading_runs'],
  pacing_rhythm: ['cpm', 'pause_count', 'long_silence_count', 'duration_ms', 'slide_duration_ms'],
  vocal_delivery: ['voice_diff_db', 'low_ratio'],
  fluency: ['filler_per_min', 'filler_count'],
  content_delivery: ['content_coverage', 'critical_fact_accuracy'],
};

/** 비교 카드에서 숨기는 지표 — 군더더기는 횟수 열만 둡니다 (#929). 미션 결과 · 근거 줄에는 그대로 보입니다 */
const HIDDEN_IN_COMPARISON = new Set(['filler_per_min']);

type ComparisonRow = ReviewResult['comparison'][number];

export interface ComparisonGroup {
  /** 차원 표에 없는 지표는 null — AI 가 지표를 늘려도 버리지 않고 맨 뒤에 모읍니다 */
  dimension: Dimension | null;
  /** 열. 차원 표 순서 */
  metrics: string[];
  /** 행. 발표 전체(null)가 먼저, 장은 번호순 */
  slides: (number | null)[];
  cell: (metric: string, slide: number | null) => ComparisonRow | undefined;
}

/** 비교 행을 차원별 표로 묶습니다 — 지표 하나가 열 하나, 발표 전체 · 장이 행입니다 (#929) */
export function comparisonGroups(rows: ReviewResult['comparison']): ComparisonGroup[] {
  const shown = rows.filter((r) => !HIDDEN_IN_COMPARISON.has(r.metric));
  const known = new Set(Object.values(DIMENSION_METRICS).flat());
  const columns: [Dimension | null, string[]][] = [
    ...(Object.entries(DIMENSION_METRICS) as [Dimension, readonly string[]][]).map(
      ([d, metrics]): [Dimension, string[]] => [
        d,
        metrics.filter((m) => shown.some((r) => r.metric === m)),
      ],
    ),
    [null, [...new Set(shown.map((r) => r.metric).filter((m) => !known.has(m)))]],
  ];

  return columns
    .filter(([, metrics]) => metrics.length > 0)
    .map(([dimension, metrics]) => {
      const own = shown.filter((r) => metrics.includes(r.metric));
      const slides = [...new Set(own.map((r) => r.slide_number))].sort(
        (a, b) => (a ?? 0) - (b ?? 0),
      );
      return {
        dimension,
        metrics,
        slides,
        cell: (metric, slide) => own.find((r) => r.metric === metric && r.slide_number === slide),
      };
    });
}

/** 없음은 0% 가 아니라 — 로 (CLAUDE.md 6번) */
export function pct(v: number | null) {
  return v === null ? '—' : `${Math.round(v * 100)}%`;
}

/** 발표 시간을 모르면 00:00 이 아니라 — 로 (CLAUDE.md 6번) */
export function duration(ms: number | null) {
  return ms === null ? '—' : formatDuration(ms);
}

export function where(area: string, slide: number | null) {
  const label = Object.hasOwn(AREA, area) ? AREA[area as Area] : area;
  return slide === null ? `${label} · 발표 전체` : `${label} · ${slide}번 장`;
}

/** ISO 시각 → `2026. 10. 6. 오후 4:38` */
export function endedAt(iso: string | null) {
  if (iso === null) return null;
  return new Date(iso).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}
