import { http, HttpResponse } from 'msw';
import type {
  ContentStatus,
  CoverageSummary,
  KeyPointCounts,
  Metric,
  SlideCoverageDetail,
  SlideSummary,
  SpeechMix,
} from '@/types/report';

/**
 * 대본 전달도 상세 목 (BE 미구현 — #1153). 숫자는 #1152 '계약: 응답에 더하는 선택 필드'의 예시 그대로입니다.
 * 말의 구성 · 대본 밖 구간의 이유 코드는 기본 설정(LLM 세부 분류 꺼짐)에서 오는 `OFFSCRIPT_DISABLED` 입니다(#1152 최종 일치 확인).
 *
 * 리포트(`mocks/report.ts`)는 Take id 마다 `content_status` 하나를 고릅니다.
 *   t3 · 그 밖                MEASURED — #1152 'Take 상세' 예시 (3장 채점 · 4장 간이, 일부 장 제외)
 *   t-content-none            null — BE 배포 전 응답
 *   t-content-analyzing       ANALYZING
 *   t-content-legacy          NOT_MEASURED `LEGACY_RESULT`
 *   t-content-failed          FAILED `AI_TIMEOUT`
 *   t-content-estimated       MEASURED — 채점된 장 없이 간이 장만 (#1152 '모든 대상 장이 실패해도' 예시)
 *   t-content-stt-cut         MEASURED — 대상 장이 모두 STT 끊김: 채점 · 간이 장 없이 장마다 `STT_INCOMPLETE` (#1152 결정, #1158 리뷰)
 *   t-content-zero            MEASURED — 전달률 0 · 수치 모두 확인 대기 · 0 의 세 종류 · 실패 장
 *   t-content-revised         MEASURED — 장을 처음 펼치면 409 `COVERAGE_REVISION_CHANGED` 한 번, 판이 0 → 1
 *
 * 장별 상세(`GET /api/takes/:takeId/script-coverage/slides/:slide`)는 장 번호로 고릅니다.
 *   3 SCORED · 4 ESTIMATED · 그 밖에 요약에 있는 장 FAILED · 요약에 없는 장 404 `SLIDE_NOT_FOUND`
 */

const CRITERIA = 'script_coverage-1.2+a1b2c3d4';

const metric = (
  value: number | null,
  state: Metric['state'],
  reason_codes: string[],
  evaluated: number,
  excluded: number,
): Metric => ({ value, state, reason_codes, evaluated, excluded });

const zeroCounts = { covered: 0, partial: 0, missing: 0, contradicted: 0 };
const noKeyPoints: KeyPointCounts = { critical: zeroCounts, high: zeroCounts, normal: zeroCounts };
const noMix: SpeechMix['categories'] = {
  script: 0,
  other_slide: 0,
  procedural: 0,
  off_script: 0,
  related_aside: 0,
  unrelated: 0,
  undetermined: 0,
};

const exampleDisplay: SpeechMix['display'] = [
  { category: 'script', tokens: 30, share: 0.75 },
  { category: 'procedural', tokens: 4, share: 0.1 },
  { category: 'off_script', tokens: 6, share: 0.15 },
];

/** #1152 'Take 상세' 예시 */
export const coverageSummaryFixture: CoverageSummary = {
  version: 1,
  delivery: metric(0.5, 'provisional', ['SLIDES_EXCLUDED'], 3, 4),
  zero_kind: null,
  contradiction: metric(0.333, 'provisional', ['SLIDES_EXCLUDED'], 3, 4),
  status_shares: { covered: 0.5, partial: 0.0, missing: 0.167, contradicted: 0.333 },
  key_points: {
    critical: { covered: 1, partial: 0, missing: 0, contradicted: 0 },
    high: { covered: 0, partial: 0, missing: 0, contradicted: 1 },
    normal: { covered: 0, partial: 0, missing: 1, contradicted: 0 },
  },
  critical_gaps: [
    {
      slide_number: 3,
      key_point_id: 'KP2',
      content: '신규 고객 비중 60%',
      importance: 'high',
      status: 'contradicted',
      said_in_slides: [],
    },
  ],
  legacy: { content_coverage_raw: 0.333 },
  facts: {
    matched: 2,
    approximate: 0,
    mismatched: 0,
    missing: 0,
    pending: 1,
    accuracy: metric(1.0, 'provisional', ['FACTS_PENDING', 'SLIDES_EXCLUDED'], 2, 1),
    accuracy_range: { best: 1.0, worst: 0.667 },
  },
  expression: { verbatim_like: 1, paraphrased: 1, undetermined: 0 },
  fidelity: {
    recall: metric(0.62, 'provisional', ['SLIDES_EXCLUDED'], 24, 0),
    precision: metric(0.81, 'provisional', ['SLIDES_EXCLUDED'], 30, 0),
  },
  speech_mix: {
    state: 'provisional',
    reason_codes: ['OFFSCRIPT_DISABLED', 'SLIDES_EXCLUDED'],
    tokens: 40,
    categories: { ...noMix, script: 30, procedural: 4, off_script: 6 },
    display: exampleDisplay,
    repetitions: { count: 2, confidence: 'low' },
  },
  scope: {
    target_slides: 2,
    scored_slides: 1,
    estimated_slides: 1,
    provisional_slides: 0,
    excluded: [{ slide_number: 4, reason_code: 'LLM_TIMEOUT' }],
  },
  slides: [
    {
      slide_number: 3,
      state: 'scored',
      reason_codes: [],
      stt_integrity: 'complete',
      rubric_quality: 'full',
      delivery: metric(0.5, 'ok', [], 3, 0),
      zero_kind: null,
      contradiction: metric(0.333, 'ok', [], 3, 0),
      status_shares: { covered: 0.5, partial: 0.0, missing: 0.167, contradicted: 0.333 },
      critical_gap_count: 1,
      delivery_band: null,
      speech_mix: { state: 'ok', reason_codes: ['OFFSCRIPT_DISABLED'], display: exampleDisplay },
    },
    {
      slide_number: 4,
      state: 'estimated',
      reason_codes: ['LLM_TIMEOUT'],
      stt_integrity: 'complete',
      rubric_quality: 'full',
      delivery: null,
      zero_kind: null,
      contradiction: null,
      status_shares: null,
      critical_gap_count: 0,
      delivery_band: 'about_half',
      speech_mix: null,
    },
  ],
};

const failedAll = (excluded: number) => metric(null, 'failed', ['SLIDES_EXCLUDED'], 0, excluded);

/** #1152 '모든 대상 장이 실패해도' 예시 — 채점된 장 없이 간이 장만 */
const estimatedOnlySummary: CoverageSummary = {
  version: 1,
  delivery: failedAll(4),
  zero_kind: null,
  contradiction: failedAll(4),
  status_shares: null,
  key_points: noKeyPoints,
  critical_gaps: [],
  legacy: { content_coverage_raw: null },
  facts: {
    matched: 0,
    approximate: 0,
    mismatched: 0,
    missing: 0,
    pending: 0,
    accuracy: failedAll(0),
    accuracy_range: null,
  },
  expression: { verbatim_like: 0, paraphrased: 0, undetermined: 0 },
  fidelity: { recall: failedAll(0), precision: failedAll(0) },
  speech_mix: {
    state: 'failed',
    reason_codes: ['SLIDES_EXCLUDED'],
    tokens: 0,
    categories: noMix,
    display: [],
    repetitions: { count: 0, confidence: 'low' },
  },
  scope: {
    target_slides: 1,
    scored_slides: 0,
    estimated_slides: 1,
    provisional_slides: 0,
    excluded: [{ slide_number: 1, reason_code: 'LLM_TIMEOUT' }],
  },
  slides: [
    {
      slide_number: 1,
      state: 'estimated',
      reason_codes: ['LLM_TIMEOUT'],
      stt_integrity: 'unknown',
      rubric_quality: 'full',
      delivery: null,
      zero_kind: null,
      contradiction: null,
      status_shares: null,
      critical_gap_count: 0,
      delivery_band: 'mostly',
      speech_mix: null,
    },
  ],
};

/** 대상 장이 모두 STT 끊김 — 예전에는 분석 거절(`AI_REJECTED`)이었고, 이제 MEASURED 로 옵니다(#1152 결정, #1158 리뷰).
 *  채점된 장도 간이 장도 없어 Take 지표는 null · failed(`SLIDES_EXCLUDED`), 장은 모두 failed(`STT_INCOMPLETE`)입니다 */
const sttCutSummary: CoverageSummary = {
  ...estimatedOnlySummary,
  delivery: failedAll(2),
  contradiction: failedAll(2),
  scope: {
    target_slides: 2,
    scored_slides: 0,
    estimated_slides: 0,
    provisional_slides: 0,
    excluded: [
      { slide_number: 1, reason_code: 'STT_INCOMPLETE' },
      { slide_number: 2, reason_code: 'STT_INCOMPLETE' },
    ],
  },
  slides: [1, 2].map((slide_number) => ({
    slide_number,
    state: 'failed' as const,
    reason_codes: ['STT_INCOMPLETE'],
    stt_integrity: 'incomplete' as const,
    rubric_quality: null,
    delivery: null,
    zero_kind: null,
    contradiction: null,
    status_shares: null,
    critical_gap_count: 0,
    delivery_band: null,
    speech_mix: null,
  })),
};

const zeroSlide = (
  slide_number: number,
  zero_kind: SlideSummary['zero_kind'],
  status_shares: SlideSummary['status_shares'],
  reason_codes: string[] = [],
): SlideSummary => ({
  slide_number,
  state: 'scored',
  reason_codes,
  stt_integrity: 'complete',
  rubric_quality: 'full',
  delivery: metric(0, 'ok', [], 2, 0),
  zero_kind,
  contradiction: metric(status_shares?.contradicted ?? 0, 'ok', [], 2, 0),
  status_shares,
  critical_gap_count: zero_kind === 'slide_skipped' ? 0 : 1,
  delivery_band: null,
  speech_mix: null,
});

/** 전달률 0 · 수치 모두 확인 대기 · 0 의 세 종류 · 문장 단위 평가 장 · 실패 장 · 핵심 내용 없는 장 */
const zeroSummary: CoverageSummary = {
  ...coverageSummaryFixture,
  delivery: metric(0, 'ok', [], 6, 0),
  zero_kind: 'missing_or_contradicted',
  contradiction: metric(0.5, 'ok', [], 6, 0),
  status_shares: { covered: 0, partial: 0, missing: 0.5, contradicted: 0.5 },
  key_points: {
    critical: { covered: 0, partial: 0, missing: 1, contradicted: 1 },
    high: { covered: 0, partial: 0, missing: 2, contradicted: 1 },
    normal: { covered: 0, partial: 0, missing: 0, contradicted: 1 },
  },
  critical_gaps: [
    {
      slide_number: 1,
      key_point_id: 'KP1',
      content: '지난 분기 매출 37% 증가',
      importance: 'critical',
      status: 'missing',
      said_in_slides: [2],
    },
    {
      slide_number: 2,
      key_point_id: 'KP3',
      content: '고객 이탈률 12%',
      importance: 'critical',
      status: 'contradicted',
      said_in_slides: [],
    },
    {
      slide_number: 2,
      key_point_id: 'KP4',
      content: '경쟁사 대비 가격 20% 낮음',
      importance: 'high',
      status: 'missing',
      said_in_slides: [],
    },
    {
      slide_number: 3,
      key_point_id: 'KP5',
      content: '파일럿 고객 3곳',
      importance: 'high',
      status: 'missing',
      said_in_slides: [],
    },
    {
      slide_number: 3,
      key_point_id: 'KP6',
      content: '하반기 출시 일정',
      importance: 'high',
      status: 'contradicted',
      said_in_slides: [],
    },
    {
      slide_number: 3,
      key_point_id: 'KP7',
      content: '팀 인원 8명',
      importance: 'high',
      status: 'missing',
      said_in_slides: [1],
    },
  ],
  legacy: { content_coverage_raw: 0 },
  facts: {
    matched: 0,
    approximate: 0,
    mismatched: 0,
    missing: 0,
    pending: 3,
    accuracy: metric(null, 'pending', ['ALL_FACTS_PENDING'], 0, 3),
    accuracy_range: { best: 0.667, worst: 0 },
  },
  expression: { verbatim_like: 0, paraphrased: 2, undetermined: 1 },
  speech_mix: {
    state: 'not_applicable',
    reason_codes: ['NO_SPEECH'],
    tokens: 0,
    categories: noMix,
    display: [],
    repetitions: { count: 0, confidence: 'low' },
  },
  scope: {
    target_slides: 6,
    scored_slides: 5,
    estimated_slides: 0,
    provisional_slides: 1,
    excluded: [{ slide_number: 6, reason_code: 'STT_INCOMPLETE' }],
  },
  slides: [
    zeroSlide(1, 'slide_skipped', { covered: 0, partial: 0, missing: 1, contradicted: 0 }, [
      'STT_UNVERIFIED',
    ]),
    zeroSlide(2, 'all_missing', { covered: 0, partial: 0, missing: 1, contradicted: 0 }),
    // 채점은 됐지만 음성 인식이 끊겨 잠정인 장 (#1157 리뷰)
    {
      ...zeroSlide(
        3,
        'missing_or_contradicted',
        { covered: 0, partial: 0, missing: 0.5, contradicted: 0.5 },
        ['STT_INCOMPLETE'],
      ),
      stt_integrity: 'incomplete',
      delivery: metric(0, 'provisional', ['STT_INCOMPLETE'], 2, 0),
    },
    {
      ...zeroSlide(4, null, null, ['NO_KEY_POINTS']),
      delivery: metric(null, 'not_applicable', ['NO_KEY_POINTS'], 0, 0),
      contradiction: metric(null, 'not_applicable', ['NO_KEY_POINTS'], 0, 0),
      critical_gap_count: 0,
    },
    {
      ...zeroSlide(
        5,
        'missing_or_contradicted',
        { covered: 0, partial: 0, missing: 0, contradicted: 1 },
        ['SENTENCE_FALLBACK'],
      ),
      rubric_quality: 'sentence_fallback',
    },
    {
      slide_number: 6,
      state: 'failed',
      reason_codes: ['STT_INCOMPLETE'],
      stt_integrity: 'incomplete',
      rubric_quality: null,
      delivery: null,
      zero_kind: null,
      contradiction: null,
      status_shares: null,
      critical_gap_count: 0,
      delivery_band: null,
      speech_mix: null,
    },
  ],
};

const measured = (summary: CoverageSummary, revision = 0): ContentStatus => ({
  state: 'MEASURED',
  reason_code: null,
  revision,
  criteria_version: CRITERIA,
  summary,
  // #1153 5절 예시 — 바로 앞 Take 하나
  previous: {
    take_id: 't2',
    take_number: 2,
    criteria_version: CRITERIA,
    delivery: metric(0.42, 'ok', [], 20, 0),
    contradiction: metric(0.05, 'ok', [], 20, 0),
    scope: {
      target_slides: 8,
      scored_slides: 8,
      estimated_slides: 0,
      provisional_slides: 0,
      excluded: [],
    },
  },
});

const unmeasured = (state: ContentStatus['state'], reason_code: string | null): ContentStatus => ({
  state,
  reason_code,
  revision: null,
  criteria_version: null,
  summary: null,
  previous: null,
});

export const contentMeasured = measured(coverageSummaryFixture);
export const contentAnalyzing = unmeasured('ANALYZING', null);
export const contentTakeFailed = unmeasured('NOT_MEASURED', 'TAKE_FAILED');

/** 판이 바뀌는 Take — 장을 처음 펼치면 409 를 한 번 주고 판을 올립니다 */
export const REVISED_TAKE_ID = 't-content-revised';
const revisionOf = new Map<string, number>([[REVISED_TAKE_ID, 0]]);

/** 시험마다 판을 처음(0)으로 되돌립니다 — 409 를 다시 보려고 */
export function resetRevisedTake() {
  revisionOf.set(REVISED_TAKE_ID, 0);
}

/** Take id 로 고르는 `content_status`. 목록에 없으면 `fallbackStatus` */
export function contentStatusOf(
  takeId: string,
  fallbackStatus: ContentStatus | null,
): ContentStatus | null {
  switch (takeId) {
    case 't-content-none':
      return null;
    case 't-content-analyzing':
      return contentAnalyzing;
    case 't-content-legacy':
      return unmeasured('NOT_MEASURED', 'LEGACY_RESULT');
    case 't-content-failed':
      return unmeasured('FAILED', 'AI_TIMEOUT');
    case 't-content-estimated':
      return { ...measured(estimatedOnlySummary), previous: null };
    case 't-content-stt-cut':
      return { ...measured(sttCutSummary), previous: null };
    case 't-content-zero':
      return measured(zeroSummary);
    case REVISED_TAKE_ID:
      return measured(coverageSummaryFixture, revisionOf.get(REVISED_TAKE_ID) ?? 0);
    default:
      return fallbackStatus;
  }
}

/** #1153 6절 예시를 늘린 3장 — 한글 · 이모지가 섞인 문장에서도 `<mark>` 위치가 맞는지 봅니다 */
export const slideScoredFixture: Omit<SlideCoverageDetail, 'take_id' | 'revision'> = {
  slide_number: 3,
  state: 'SCORED',
  detail: {
    version: 1,
    state: 'ok',
    reason_codes: [],
    stt_integrity: 'complete',
    rubric_quality: 'full',
    delivery: metric(0.5, 'ok', [], 3, 0),
    zero_kind: null,
    contradiction: metric(0.333, 'ok', [], 3, 0),
    status_shares: { covered: 0.5, partial: 0.0, missing: 0.167, contradicted: 0.333 },
    key_points: coverageSummaryFixture.key_points,
    critical_gaps: [
      {
        key_point_id: 'KP2',
        content: '신규 고객 비중 60%',
        importance: 'high',
        status: 'contradicted',
        said_in_slides: [],
      },
    ],
    legacy: { content_coverage_raw: 0.333, zero_kind: null },
    facts: {
      matched: 2,
      approximate: 0,
      mismatched: 0,
      missing: 0,
      pending: 1,
      accuracy: metric(1.0, 'provisional', ['FACTS_PENDING'], 2, 1),
      accuracy_range: { best: 1.0, worst: 0.667 },
    },
    expression: { verbatim_like: 1, paraphrased: 1, undetermined: 0 },
    fidelity: {
      recall: metric(0.62, 'ok', [], 24, 0),
      precision: metric(0.81, 'ok', [], 30, 0),
    },
    speech_mix: {
      state: 'ok',
      reason_codes: ['OFFSCRIPT_DISABLED'],
      tokens: 40,
      categories: { ...noMix, script: 30, procedural: 4, off_script: 6 },
      display: exampleDisplay,
      repetitions: { count: 2, confidence: 'low' },
    },
  },
  offscript: {
    version: 1,
    state: 'ok',
    reason_codes: ['OFFSCRIPT_DISABLED'],
    spans: [
      {
        id: 'O1',
        stt_sentence_index: 0,
        start: 0,
        end: 5,
        category: 'procedural',
        display_category: 'procedural',
        source: 'rule',
        other_slide_number: null,
        tokens: 2,
      },
      // "🙂 " 다음 — 이모지는 코드 포인트 하나, UTF-16 으로는 둘입니다
      {
        id: 'O2',
        stt_sentence_index: 2,
        start: 2,
        end: 12,
        category: 'related_aside',
        display_category: 'off_script',
        source: 'llm',
        other_slide_number: null,
        tokens: 3,
      },
    ],
  },
  failure: null,
  sentences: [
    {
      sentence_index: 0,
      text: '지난 분기 매출은 37% 늘었습니다.',
      role: 'claim',
      status: 'said',
      expression_mode: 'paraphrased',
      reason: '숫자와 증가를 모두 말했습니다.',
      evidence: [0],
      units: [{ unit_id: 'S0-U1', text: '매출 37% 증가', status: 'said' }],
    },
    {
      sentence_index: 1,
      text: '신규 고객 비중은 60%입니다.',
      role: 'evidence',
      status: 'contradicted',
      expression_mode: null,
      reason: '40%라고 말했습니다.',
      evidence: [1],
      units: [{ unit_id: 'S1-U1', text: '신규 고객 비중 60%', status: 'contradicted' }],
    },
    {
      sentence_index: 2,
      text: '이 수치는 회계 기준으로 집계했습니다.',
      role: 'detail',
      status: 'missing',
      expression_mode: null,
      reason: '말하지 않았습니다.',
      evidence: [],
      units: [{ unit_id: 'S2-U1', text: '회계 기준 집계', status: 'missing' }],
    },
    {
      sentence_index: 3,
      text: '(다음 장으로 넘어갑니다)',
      role: 'skip',
      status: null,
      expression_mode: null,
      reason: '진행 문장이라 채점하지 않습니다.',
      evidence: [],
      units: [],
    },
  ],
  key_points: [
    {
      key_point_id: 'KP1',
      content: '지난 분기 매출 37% 증가',
      importance: 'critical',
      status: 'covered',
      reason: '그대로 말했습니다.',
      evidence: [0],
    },
    {
      key_point_id: 'KP2',
      content: '신규 고객 비중 60%',
      importance: 'high',
      status: 'contradicted',
      reason: '40%라고 말했습니다.',
      evidence: [1],
    },
    {
      key_point_id: 'KP3',
      content: '회계 기준 집계',
      importance: 'normal',
      status: 'missing',
      reason: '말하지 않았습니다.',
      evidence: [],
    },
  ],
  critical_facts: [
    { fact_id: 'CF1', type: 'percentage', value: '37%', stt_value: '37%', status: 'matched' },
    { fact_id: 'CF2', type: 'percentage', value: '60%', stt_value: '40%', status: 'mismatched' },
    { fact_id: 'CF3', type: 'count', value: '3곳', stt_value: null, status: 'pending' },
  ],
  stt_sentences: [
    '안녕하세요 지난 분기 매출은 삼십칠 퍼센트 늘었는데요',
    '신규 고객 비중은 사십 퍼센트입니다',
    '🙂 사실 저도 놀랐어요 아무튼 다음으로',
  ],
};

/** #1152 '실패 장 간이 값' 예시 — 4장 */
export const slideEstimatedFixture: Omit<SlideCoverageDetail, 'take_id' | 'revision'> = {
  slide_number: 4,
  state: 'ESTIMATED',
  detail: null,
  offscript: null,
  failure: {
    error: { code: 'LLM_TIMEOUT', message: 'LLM 응답 시간이 초과되었습니다' },
    fallback: {
      version: 1,
      method: 'lexical',
      delivery_band: 'about_half',
      lexical_coverage: 0.48,
      facts: { matched: 1, approximate: 0, mismatched: 0, missing: 1, pending: 0 },
      fact_accuracy: metric(0.5, 'estimated', [], 2, 0),
      script_recall: metric(0.41, 'estimated', [], 22, 0),
      speech_mix: null,
      stt_integrity: 'complete',
      reason_codes: ['LLM_TIMEOUT'],
    },
  },
  sentences: [],
  key_points: [],
  critical_facts: [],
  stt_sentences: [],
};

/** 간이 값도 없는 실패 장. 예전 결과처럼 `fallback` 키가 빠져 있어 훅이 null 로 맞춥니다 */
const slideFailedFixture = (slide_number: number, code: string) => ({
  slide_number,
  state: 'FAILED' as const,
  detail: null,
  offscript: null,
  failure: { error: { code, message: '' } },
  sentences: [],
  key_points: [],
  critical_facts: [],
  stt_sentences: [],
});

function slideOf(summary: CoverageSummary, slideNumber: number) {
  const slide = summary.slides.find((s) => s.slide_number === slideNumber);
  if (!slide) return null;
  if (slide.state === 'scored') {
    // 장 요약의 이유 · 평가 방식을 상세에도 맞춥니다. 상세 상태는 이유가 있는지가 아니라 장 요약의 지표 상태를 따릅니다 —
    // 이유가 있어도 복구 기준을 채우면 ok 입니다 (#1152, #1157 리뷰)
    const detail = slideScoredFixture.detail!;
    const provisional =
      slide.delivery?.state === 'provisional' || slide.contradiction?.state === 'provisional';
    return {
      ...slideScoredFixture,
      slide_number: slideNumber,
      detail: {
        ...detail,
        state: provisional ? ('provisional' as const) : detail.state,
        reason_codes: slide.reason_codes,
        stt_integrity: slide.stt_integrity,
        rubric_quality: slide.rubric_quality ?? detail.rubric_quality,
      },
    };
  }
  if (slide.state === 'estimated') {
    const fallback = slideEstimatedFixture.failure!.fallback!;
    return {
      ...slideEstimatedFixture,
      slide_number: slideNumber,
      failure: {
        error: { code: slide.reason_codes[0] ?? 'LLM_ERROR', message: '' },
        fallback: { ...fallback, delivery_band: slide.delivery_band },
      },
    };
  }
  return slideFailedFixture(slideNumber, slide.reason_codes[0] ?? 'LLM_ERROR');
}

const notFound = (code: string) => HttpResponse.json({ code, message: code }, { status: 404 });

/** 장별 상세 핸들러. `reportStatus` 는 리포트 목과 같은 `content_status` 를 돌려줍니다 */
export function contentCoverageHandlers(reportStatus: (takeId: string) => ContentStatus | null) {
  return [
    http.get('*/api/takes/:takeId/script-coverage/slides/:slide', ({ params, request }) => {
      const takeId = String(params.takeId);
      const slideNumber = Number(params.slide);
      const asked = Number(new URL(request.url).searchParams.get('revision'));
      if (takeId === REVISED_TAKE_ID && revisionOf.get(takeId) === 0) {
        revisionOf.set(takeId, 1);
        return HttpResponse.json(
          { code: 'COVERAGE_REVISION_CHANGED', message: 'revision changed' },
          { status: 409 },
        );
      }
      const status = reportStatus(takeId);
      if (!status?.summary) return notFound('COVERAGE_NOT_FOUND');
      if (status.revision !== null && asked !== status.revision)
        return HttpResponse.json(
          { code: 'COVERAGE_REVISION_CHANGED', message: 'revision changed' },
          { status: 409 },
        );
      const slide = slideOf(status.summary, slideNumber);
      if (!slide) return notFound('SLIDE_NOT_FOUND');
      return HttpResponse.json({ ...slide, take_id: takeId, revision: status.revision ?? 0 });
    }),
  ];
}
