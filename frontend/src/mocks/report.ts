import { http, HttpResponse } from 'msw';
import type { AnalysisStage, ReviewResult, TakeMeasures, TakeReport } from '@/types/report';

/**
 * Take 리포트 목 — `GET /api/takes/{take_id}/report` (BE 에 아직 없는 API, `types/report.ts`).
 *
 * Take id 마다 상태 하나를 고정해 둡니다. 화면 상태를 주소만 바꿔 볼 수 있게 하려는 것입니다.
 *   t2              시선 측정 제외 — 시선 비율이 0이 아니라 null
 *   t3              정상 — 리뷰까지 끝남
 *   t-fallback      AI 문장 실패 — 숫자 · 판정은 같고 문장만 기본 문장
 *   t-review-failed 리뷰 실패 — 측정값만
 *   t-failed        분석 실패
 *   t-held          리뷰 보류 — 사람이 복구할 때까지 멈춤
 *   그 밖의 id      처음 물은 뒤로 몇 초마다 단계가 넘어가 정상으로 끝남 (리허설을 끝내고 온 Take)
 */

export const measuresFixture: TakeMeasures = {
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
export const reviewFixture: ReviewResult = {
  take_id: 't3',
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

/** 홈 · 준비 목의 p1 과 같은 피치입니다 — 화면마다 제목이 어긋나지 않게 */
const done: TakeReport = {
  take_id: 't3',
  pitch_id: 'p1',
  pitch_title: '캡스톤 최종 발표',
  take_number: 3,
  script_mode: 'HIGHLIGHT',
  ended_at: '2026-10-06T16:38:00+09:00',
  status: 'COMPLETED',
  analysis_stage: 'DONE',
  review_status: 'DONE',
  analysis_hold_reason: null,
  measures: measuresFixture,
  review: reviewFixture,
};

/** 5-5 경계 — LLM 실패. 숫자 · 상태 · 점수는 같고 문장만 기본 문장입니다 */
const fallback: TakeReport = {
  ...done,
  take_id: 't-fallback',
  review: {
    ...reviewFixture,
    take_id: 't-fallback',
    overall_summary: '먼저 고칠 차원: 대본 의존(3번 장).',
    strengths: [{ ...reviewFixture.strengths[0]!, text: '내용 전달: 핵심 내용 전달률 0.92' }],
    fallback_reason: 'LLM_TIMEOUT',
  },
};

const reviewFailed: TakeReport = {
  ...done,
  take_id: 't-review-failed',
  review_status: 'FAILED',
  review: null,
};

const failed: TakeReport = {
  ...done,
  take_id: 't-failed',
  status: 'FAILED',
  analysis_stage: null,
  review_status: null,
  measures: null,
  review: null,
};

/** 리뷰 보류 — 대본 전달도 재채점이 끝내 실패해 사람이 복구할 때까지 멈춤 (`architecture.md` 1-3) */
const held: TakeReport = {
  ...done,
  take_id: 't-held',
  status: 'ANALYZING',
  analysis_stage: 'SCORING',
  review_status: null,
  analysis_hold_reason: 'COVERAGE_RESCORE_FAILED',
  review: null,
};

/** 시선 측정 제외 — 0이 아니라 null (CLAUDE.md 6번). 이 케이스를 목에 꼭 남겨두세요 */
const gazeExcluded: TakeReport = {
  ...done,
  take_id: 't2',
  take_number: 2,
  measures: {
    ...measuresFixture,
    audience_ratio: null,
    screen_ratio: null,
    script_ratio: null,
    away_ratio: null,
    unmeasured_ms: null,
  },
};

const FIXED: Record<string, TakeReport> = {
  t2: gazeExcluded,
  t3: done,
  't-fallback': fallback,
  't-review-failed': reviewFailed,
  't-failed': failed,
  't-held': held,
};

/** 단계 하나에 걸리는 시간. 대본 확인 단계도 한 번 거칩니다 */
const STAGE_MS = 2500;
const STAGES: AnalysisStage[] = ['FINALIZING', 'SCORING', 'AWAITING_CONFIRMATION', 'REVIEWING'];
const firstAsked = new Map<string, number>();

function progressing(takeId: string): TakeReport {
  const since = firstAsked.get(takeId) ?? Date.now();
  firstAsked.set(takeId, since);
  const stage = STAGES[Math.floor((Date.now() - since) / STAGE_MS)];
  if (!stage) return { ...done, take_id: takeId, take_number: 4 };
  return {
    ...done,
    take_id: takeId,
    take_number: 4,
    status: 'ANALYZING',
    analysis_stage: stage,
    review_status: null,
    measures: stage === 'FINALIZING' ? null : measuresFixture,
    review: null,
  };
}

export const reportHandlers = [
  http.get('*/api/takes/:takeId/report', ({ params }) => {
    const takeId = String(params.takeId);
    return HttpResponse.json(FIXED[takeId] ?? progressing(takeId));
  }),
];
