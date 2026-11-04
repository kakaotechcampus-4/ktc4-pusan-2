/**
 * 리포트 시안용 데이터. **시안 전용입니다** — 실제 화면은 BE 리포트 API가 정해진 뒤 `types/api.ts`로 옮깁니다.
 *
 * 리뷰 결과(`ReviewResult`)는 AI 리뷰 에이전트 출력 그대로입니다 (`docs/ai/features/review-agent.md` 5-3 · 5-5).
 * 측정값(`TakeMeasures`)은 코치 Take 결과에서 오는 값이고, BE가 어떤 모양으로 합쳐 줄지는 아직 정하지 않았습니다.
 * 이름만 AI 지표 이름(`docs/ai/architecture.md` 6-3)에 맞춰 두었습니다.
 */

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

interface Point {
  area: Area;
  slide_number: number | null;
  text: string;
  evidence: Record<string, number>;
}

export interface ReviewResult {
  take_id: string;
  overall_summary: string;
  score: number | null;
  dimensions: Record<Dimension, DimensionStatus>;
  strengths: Point[];
  improvements: Point[];
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

/** 리뷰가 없어도 보이는 측정값 (코치 Take 결과) */
export interface TakeMeasures {
  pitch_title: string;
  take_number: number;
  ended_at: string;
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

export const measures: TakeMeasures = {
  pitch_title: '캡스톤 중간발표',
  take_number: 4,
  ended_at: '2026.10.06 16:38',
  duration_ms: 278_000,
  goal_ms: 300_000,
  audience_ratio: 0.74,
  screen_ratio: 0.03,
  script_ratio: 0.22,
  away_ratio: 0.01,
  unmeasured_ms: 17_000,
  cpm: 352,
  filler_count: 7,
};

/** `review-agent.md` 5-5 응답 예시 */
export const review: ReviewResult = {
  take_id: 'take-123',
  overall_summary: '속도는 안정됐지만 3번 장에서 대본을 이어 읽는 시간이 길었습니다.',
  score: 74,
  dimensions: {
    audience_gaze: 'STABLE',
    script_independence: 'PRIORITY',
    pacing_rhythm: 'STABLE',
    vocal_delivery: 'STRENGTH',
    fluency: 'IMPROVED',
    content_delivery: 'STRENGTH',
  },
  strengths: [
    {
      area: 'CONTENT',
      slide_number: null,
      text: '핵심 내용을 빠짐없이 전달했습니다',
      evidence: { content_coverage: 0.92 },
    },
  ],
  improvements: [
    {
      area: 'GAZE',
      slide_number: 3,
      text: '수치를 읽을 때도 문장 끝에서 고개를 들어 보세요',
      evidence: { script_ratio: 0.62 },
    },
  ],
  comparison: [
    {
      metric: 'script_ratio',
      slide_number: 3,
      previous: 0.7,
      current: 0.62,
      improved: true,
      script_mode: null,
    },
  ],
  mission_results: [
    {
      mission_id: 'm-1',
      status: 'PARTIAL',
      metric: 'filler_per_min',
      operator: 'LTE',
      slide_number: null,
      target: 2,
      observed: 2.4,
    },
  ],
  criteria_results: [
    {
      display: '추임새 5번 이하',
      metric: 'filler_count',
      operator: 'LTE',
      target: 5,
      observed: 7,
      status: 'FAILED',
    },
  ],
  next_missions: [
    {
      area: 'FILLER',
      slide_number: null,
      description: '추임새 5번 이하',
      target: { metric: 'filler_count', operator: 'LTE', value: 5 },
      priority: 1,
      reason: '직접 정한 기준인데 이번 Take 7번',
    },
    {
      area: 'GAZE',
      slide_number: 3,
      description: '3번 장 대본 응시 40% 이하',
      target: { metric: 'script_ratio', operator: 'LTE', value: 0.4 },
      priority: 2,
      reason: '3번 장 이번 Take 0.62, 이전 Take 0.70',
    },
  ],
  fallback_reason: null,
};

/** 5-5 경계 — LLM 실패. 숫자 · 상태 · 점수는 같고 문장만 기본 문장입니다 */
export const reviewFallback: ReviewResult = {
  ...review,
  overall_summary: '먼저 고칠 차원: 대본 의존(3번 장). 점수 74.',
  strengths: [{ ...review.strengths[0]!, text: '내용 전달: 핵심 내용 전달률 0.92' }],
  fallback_reason: 'LLM_TIMEOUT',
};
