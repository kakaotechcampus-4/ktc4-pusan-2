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
  /** First Take 튜토리얼 샘플 피치의 Take 인지 (BE #1166) */
  is_demo: boolean;
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
  /**
   * 대본 전달도 상세 (BE 미구현 — #1153 5절). BE 가 배포되기 전 응답에는 키가 없어
   * `fetchTakeReport` 가 null 로 맞춥니다. null 이면 "아직 제공하지 않아요"만 보입니다
   */
  content_status: ContentStatus | null;
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
export type Operator = 'LT' | 'LTE' | 'GT' | 'GTE';

export interface ReviewPoint {
  area: string;
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
  dimensions: Record<Dimension, string>;
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
    area: string;
    slide_number: number | null;
    description: string;
    target: { metric: string; operator: Operator; value: number };
    priority: number;
    reason: string | null;
  }[];
  fallback_reason: 'LLM_TIMEOUT' | 'LLM_ERROR' | 'LLM_INVALID_OUTPUT' | 'LLM_UNAVAILABLE' | null;
}

// ---------------------------------------------------------------------------
// 대본 전달도 상세 — AI 계약 #1152 '계약: 응답에 더하는 선택 필드', BE API #1153, FE #1154.
// BE 그대로 snake_case. (BE 미구현 — #1153) 목은 `mocks/report.ts` 가 #1152 예시 숫자로 흉내 냅니다.

/** 지표 한 칸의 상태. 모르는 값도 올 수 있어 화면은 일반 문구로 받습니다 */
export type MetricState =
  | 'ok'
  | 'provisional'
  | 'not_applicable'
  | 'pending'
  | 'failed'
  | 'skipped_by_deadline'
  | 'estimated';

/** #1152 '지표 한 칸(Metric)'. `value` 는 0~1, 없으면 null — 0 과 섞지 않습니다 */
export interface Metric {
  value: number | null;
  state: MetricState;
  reason_codes: string[];
  evaluated: number;
  excluded: number;
}

/** 핵심 내용 판정 */
export type CoverageStatus = 'covered' | 'partial' | 'missing' | 'contradicted';
/** 대본 문장 판정 */
export type SentenceStatus = 'said' | 'partial' | 'missing' | 'contradicted';
export type Importance = 'critical' | 'high' | 'normal';
export type SttIntegrity = 'complete' | 'incomplete' | 'unknown';
/** 새 전달률이 0일 때만 값이 있습니다 */
export type ZeroKind = 'slide_skipped' | 'all_missing' | 'missing_or_contradicted';
/** 실패 장의 간이 단계 — 숫자 대신 이 단계로 보입니다 */
export type DeliveryBand = 'mostly' | 'about_half' | 'little';
export type MixCategory =
  | 'script'
  | 'other_slide'
  | 'procedural'
  | 'off_script'
  | 'related_aside'
  | 'unrelated'
  | 'undetermined';

export interface MixItem {
  category: MixCategory;
  tokens: number;
  share: number;
}

/** 말의 구성. 말이 없으면 null 대신 `state: not_applicable`(`NO_SPEECH`) */
export interface SpeechMix {
  state: MetricState;
  reason_codes: string[];
  tokens: number;
  categories: Record<MixCategory, number>;
  /** 화면에 그릴 목록. 검증 전 범주는 `off_script` 로 합쳐 오고 `NOT_VALIDATED` 가 붙습니다 */
  display: MixItem[];
  repetitions: { count: number; confidence: 'low' };
}

/** 장 요약(`slides[]`)에 실리는 말의 구성 */
export type SpeechMixBrief = Pick<SpeechMix, 'state' | 'reason_codes' | 'display'>;

/** 네 판정의 가중 몫. 합이 1입니다 */
export type StatusShares = Record<CoverageStatus, number>;
export type KeyPointCounts = Record<Importance, Record<CoverageStatus, number>>;

export interface FactCounts {
  matched: number;
  approximate: number;
  mismatched: number;
  missing: number;
  /** 발음이 비슷해 확인을 기다리는 수치 */
  pending: number;
}

export interface FactSummary extends FactCounts {
  accuracy: Metric;
  /** 확인 대기 수치를 모두 맞음 · 모두 틀림으로 가정한 범위. 모두 대기면 `accuracy.value` 는 null */
  accuracy_range: { best: number; worst: number } | null;
}

export interface SlideCriticalGap {
  key_point_id: string;
  content: string;
  importance: Importance;
  status: CoverageStatus;
  /** 그 내용을 다른 장에서 말한 장 번호. 점수로 인정하지는 않습니다 */
  said_in_slides: number[];
}

export interface TakeCriticalGap extends SlideCriticalGap {
  slide_number: number;
}

/** `said` 문장 수. 바꿔 말해도 감점하지 않습니다 */
export interface Expression {
  verbatim_like: number;
  paraphrased: number;
  undetermined: number;
}

export interface Fidelity {
  recall: Metric;
  precision: Metric;
}

/** 장 상세 = AI 응답 `evaluations[].detail` */
export interface CoverageSlideDetail {
  version: number;
  state: MetricState;
  reason_codes: string[];
  stt_integrity: SttIntegrity;
  rubric_quality: 'full' | 'sentence_fallback';
  delivery: Metric;
  zero_kind: ZeroKind | null;
  contradiction: Metric;
  /** 핵심 내용이 없으면 null */
  status_shares: StatusShares | null;
  key_points: KeyPointCounts;
  critical_gaps: SlideCriticalGap[];
  legacy: {
    content_coverage_raw: number | null;
    zero_kind: 'cancelled_to_zero' | 'clamped_negative' | null;
  };
  facts: FactSummary;
  expression: Expression;
  fidelity: Fidelity;
  speech_mix: SpeechMix;
}

/** 실패 장의 간이 값 = AI 응답 `failed[].fallback` */
export interface Fallback {
  version: number;
  /** `lexical_no_rubric` 은 평가 기준 없이 대본 글로만 낸 값입니다 */
  method: 'lexical' | 'lexical_no_rubric';
  delivery_band: DeliveryBand | null;
  lexical_coverage: number | null;
  facts: FactCounts | null;
  fact_accuracy: Metric | null;
  script_recall: Metric | null;
  speech_mix: SpeechMix | null;
  stt_integrity: SttIntegrity;
  reason_codes: string[];
}

/** Take 요약 안의 장 요약 = AI 응답 `detail.slides[]` */
export interface SlideSummary {
  slide_number: number;
  /** `scored` 채점됨 · `estimated` 실패했지만 간이 값 있음 · `failed` 이유만 */
  state: 'scored' | 'estimated' | 'failed';
  reason_codes: string[];
  stt_integrity: SttIntegrity;
  rubric_quality: 'full' | 'sentence_fallback' | null;
  delivery: Metric | null;
  zero_kind: ZeroKind | null;
  contradiction: Metric | null;
  status_shares: StatusShares | null;
  critical_gap_count: number;
  delivery_band: DeliveryBand | null;
  speech_mix: SpeechMixBrief | null;
}

export interface Scope {
  target_slides: number;
  scored_slides: number;
  estimated_slides: number;
  provisional_slides: number;
  excluded: { slide_number: number; reason_code: string }[];
}

/** Take 요약 = AI 응답 맨 위 `detail` */
export interface CoverageSummary {
  version: number;
  delivery: Metric;
  zero_kind: ZeroKind | null;
  contradiction: Metric;
  status_shares: StatusShares | null;
  key_points: KeyPointCounts;
  critical_gaps: TakeCriticalGap[];
  legacy: { content_coverage_raw: number | null };
  facts: FactSummary;
  expression: Expression;
  fidelity: Fidelity;
  speech_mix: SpeechMix;
  scope: Scope;
  slides: SlideSummary[];
}

export type ContentState = 'ANALYZING' | 'MEASURED' | 'NOT_MEASURED' | 'FAILED';

/**
 * 지난 Take 비교용 — 그 Take 상세의 전달 · 다르게 말함 · 범위.
 * BE(#1156 `ContentPreviousDTO`)는 그 Take 확정본의 값을 해석하지 않고 그대로 싣고,
 * `criteria_version` 이 없으면 null 입니다. 칸이 빠진 결과도 null 로 올 수 있어 화면은 비교를 숨깁니다
 */
export interface PreviousCoverage {
  take_id: string;
  take_number: number;
  criteria_version: string | null;
  delivery: Metric | null;
  contradiction: Metric | null;
  scope: Scope | null;
}

/**
 * 리포트 응답의 `content_status` (BE 미구현 — #1153 5절). `MEASURED` 가 아니면
 * `summary` · `revision` · `criteria_version` 은 null 입니다
 */
export interface ContentStatus {
  state: ContentState;
  /** `NOT_MEASURED` · `FAILED` 의 이유 (`TAKE_FAILED` · `LEGACY_RESULT` · `NO_RESULT` · AI 실패 코드) */
  reason_code: string | null;
  /** 장별 상세를 부를 때 같이 보내는 확정본 판. 확인 답으로 결과가 바뀌면 올라갑니다 */
  revision: number | null;
  /** `script_coverage-1.2+<해시>`. 지난 Take 비교는 `+` 앞(기능 버전)이 같을 때만 보입니다 */
  criteria_version: string | null;
  summary: CoverageSummary | null;
  /** 바로 앞 Take 하나. 그 Take 에 상세가 없으면 null */
  previous: PreviousCoverage | null;
}

/** 대본 밖 구간. `start` · `end` 는 `stt_sentences[stt_sentence_index]` 안의 `[시작, 끝)` 코드 포인트 위치 */
export interface OffscriptSpan {
  id: string;
  stt_sentence_index: number;
  start: number;
  end: number;
  category: MixCategory;
  /** 화면은 이 범주를 씁니다 */
  display_category: MixCategory;
  source: 'rule' | 'llm';
  other_slide_number: number | null;
  tokens: number;
}

export type SlideCoverageState = 'SCORED' | 'ESTIMATED' | 'FAILED';

export interface CoverageSentence {
  sentence_index: number;
  text: string;
  role: 'claim' | 'evidence' | 'detail' | 'skip';
  /** `skip` 문장은 채점하지 않아 null 일 수 있습니다 */
  status: SentenceStatus | null;
  expression_mode: 'verbatim_like' | 'paraphrased' | 'undetermined' | null;
  reason: string;
  evidence: number[];
  units: { unit_id: string; text: string; status: 'said' | 'vague' | 'missing' | 'contradicted' }[];
}

export interface CoverageKeyPoint {
  key_point_id: string;
  content: string;
  importance: Importance;
  status: CoverageStatus;
  reason: string;
  evidence: number[];
}

export interface CoverageFact {
  fact_id: string;
  type: string;
  value: string;
  stt_value: string | null;
  status: string;
}

/**
 * 장별 상세 — `GET /api/takes/{take_id}/script-coverage/slides/{slide_number}?revision=` (BE 미구현 — #1153 6절).
 * 자료가 없으면 목록 칸은 `[]`, `detail` · `offscript` · `failure` 는 null 입니다
 */
export interface SlideCoverageDetail {
  take_id: string;
  revision: number;
  slide_number: number;
  state: SlideCoverageState;
  detail: CoverageSlideDetail | null;
  offscript: {
    version: number;
    state: MetricState;
    reason_codes: string[];
    spans: OffscriptSpan[];
  } | null;
  /**
   * `ESTIMATED` · `FAILED` 일 때 그 장의 실패. `fallback` 이 없는 예전 결과는 null 로 맞춥니다.
   * BE(#1156)는 저장한 `failed[]` 항목을 그대로 실어 `error` 도 빠지면 null 입니다
   */
  failure: { error: { code: string; message: string } | null; fallback: Fallback | null } | null;
  sentences: CoverageSentence[];
  key_points: CoverageKeyPoint[];
  critical_facts: CoverageFact[];
  stt_sentences: string[];
}
