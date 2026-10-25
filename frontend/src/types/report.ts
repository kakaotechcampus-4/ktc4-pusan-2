import type { ScriptMode, TakeStatus } from './api';

/**
 * Take 리포트 — `GET /api/takes/{take_id}/report`. **BE 에 아직 없는 API 입니다 (FE 제안).**
 * 분석 대기(P6-0)와 리포트(P6)가 같은 응답을 씁니다. 분석 대기는 끝날 때까지 이걸 주기적으로 묻습니다.
 *
 * BE 그대로 snake_case 입니다. 단계 · 리뷰 상태는 BE #239 의 `takes` 칸(`analysis_stage` · `review_status`),
 * 리뷰 본문은 AI 리뷰 에이전트 출력 그대로입니다 (`docs/ai/features/review-agent.md` 5-3 · 5-5).
 */
export interface TakeReport {
  take_id: string;
  pitch_id: string;
  pitch_title: string;
  take_number: number;
  script_mode: ScriptMode;
  /** ISO 8601. 끝내기를 누른 순간. 끝나지 않은 Take 는 null */
  ended_at: string | null;
  /** `takes.status`. 분석하지 못하고 끝났으면 FAILED */
  status: TakeStatus;
  /** `takes.analysis_stage` (#239). 아직 단계가 없으면 null */
  analysis_stage: AnalysisStage | null;
  /** `takes.review_status` (#239). 리뷰를 부르기 전이면 null */
  review_status: ReviewStatus | null;
  /**
   * `takes.analysis_hold_reason` (`docs/ai/architecture.md` 1-3 '리뷰 보류'). 리뷰를 멈춘 이유이고,
   * 사람이 복구할 때까지 기다려도 바뀌지 않습니다. null 이면 보류가 아닙니다
   */
  analysis_hold_reason: 'COVERAGE_RESCORE_FAILED' | null;
  /** 코치 Take 결과. 발표 기록 정리(`FINALIZING`)가 끝나기 전이면 null */
  measures: TakeMeasures | null;
  /** 리뷰가 아직이거나 실패했으면 null — 측정값만 보입니다 */
  review: ReviewResult | null;
}

/** BE #239 `check_take_analysis_stage` */
export type AnalysisStage =
  'FINALIZING' | 'SCORING' | 'AWAITING_CONFIRMATION' | 'REVIEWING' | 'DONE';

/** BE #239 `check_take_review_status` */
export type ReviewStatus = 'DONE' | 'FAILED' | 'SKIPPED';

/**
 * 리뷰가 없어도 보이는 측정값 (코치 Take 결과). BE 가 어떤 모양으로 줄지는 아직 정하지 않았고,
 * 이름만 AI 지표 이름(`docs/ai/architecture.md` 6-3)에 맞춰 두었습니다.
 */
export interface TakeMeasures {
  duration_ms: number;
  goal_ms: number;
  /** 시선 네 곳. 분모는 모두 측정한 시간이라 넷을 더하면 1입니다 (`gaze-tracking.md` 5절) */
  audience_ratio: number | null;
  screen_ratio: number | null;
  script_ratio: number | null;
  away_ratio: number | null;
  /** 측정 못 한 시간 — 비율 계산에서 빠집니다 */
  unmeasured_ms: number | null;
  cpm: number | null;
  filler_count: number | null;
}

export type Area = 'GAZE' | 'SPEED' | 'VOLUME' | 'PAUSE' | 'FILLER' | 'CONTENT' | 'TIME';
export type Dimension =
  | 'audience_gaze'
  | 'script_independence'
  | 'pacing_rhythm'
  | 'vocal_delivery'
  | 'fluency'
  | 'content_delivery';
export type DimensionStatus = 'PRIORITY' | 'IMPROVED' | 'STRENGTH' | 'STABLE' | 'NOT_EVALUABLE';
export type JudgeStatus = 'ACHIEVED' | 'PARTIAL' | 'FAILED' | 'NOT_EVALUABLE';
export type Operator = 'LTE' | 'GTE';

export interface ReviewPoint {
  area: Area;
  slide_number: number | null;
  text: string;
  evidence: Record<string, number>;
}

/** `review-agent.md` 5-3 */
export interface ReviewResult {
  take_id: string;
  overall_summary: string;
  /** 화면에 보여 주지 않습니다 — "점수가 아니라 Take 간 변화" (CLAUDE.md) */
  score: number | null;
  dimensions: Record<Dimension, DimensionStatus>;
  strengths: ReviewPoint[];
  improvements: ReviewPoint[];
  comparison: {
    metric: string;
    slide_number: number | null;
    previous: number;
    current: number;
    improved: boolean | null;
    script_mode: { previous: string; current: string } | null;
  }[];
  mission_results: {
    mission_id: string;
    status: JudgeStatus;
    metric: string;
    operator: Operator;
    slide_number: number | null;
    target: number;
    observed: number | null;
  }[];
  criteria_results: {
    display: string;
    metric: string;
    operator: Operator;
    target: number;
    observed: number | null;
    status: JudgeStatus;
  }[];
  next_missions: {
    area: Area;
    slide_number: number | null;
    description: string;
    target: { metric: string; operator: Operator; value: number };
    priority: number;
    reason: string;
  }[];
  fallback_reason: 'LLM_TIMEOUT' | 'LLM_ERROR' | 'LLM_INVALID_OUTPUT' | 'LLM_UNAVAILABLE' | null;
}
