import type {
  ContentStatus,
  CoverageStatus,
  CoverageSummary,
  DeliveryBand,
  FactSummary,
  Importance,
  Metric,
  MixCategory,
  OffscriptSpan,
  Scope,
  SentenceStatus,
  SlideSummary,
  ZeroKind,
} from '@/types/report';

/**
 * 대본 전달도 상세(#1154)의 문구와 계산을 한 곳에 둡니다. 화면(`ContentCard` · `SlideCoverage`)은 여기만 봅니다.
 * 모르는 코드 · 범주 · 상태는 `Object.hasOwn` 으로 걸러 일반 문구로 받습니다 (리포트의 다른 이름표와 같은 규칙).
 */

/** 이유 코드 → 문구 (#1154 6절, 해요체) */
const REASON: Record<string, string> = {
  NO_KEY_POINTS: '이 장에는 확인할 핵심 내용이 없어요',
  NO_FACTS: '확인할 수치가 없어요',
  FACTS_PENDING: '확인을 기다리는 수치가 있어요',
  ALL_FACTS_PENDING: '모든 수치가 확인을 기다리고 있어요',
  NO_SPEECH: '이 장에서 말한 내용이 없어요',
  STT_INCOMPLETE: '음성 인식이 끊긴 구간이 있어요. 녹음된 부분만 봤어요',
  STT_UNVERIFIED: '음성 인식이 온전했는지 확인할 수 없어요',
  SENTENCE_FALLBACK: '이 장은 문장 단위로 평가했어요(참고용)',
  SLIDES_EXCLUDED: '평가하지 못한 장을 빼고 계산했어요',
  NOT_VALIDATED: '대본 밖 말은 세부 구분 없이 합쳐 보여요',
  OFFSCRIPT_DISABLED: '대본 밖 말은 세부 구분 없이 합쳐 보여요',
  OFFSCRIPT_DEADLINE: '시간이 부족해 대본 밖 말을 나누지 못했어요',
  OFFSCRIPT_FAILED: '대본 밖 말을 나누지 못했어요',
  RUBRIC_MISSING: '이 장의 평가 기준을 만들지 못했어요',
  LLM_TIMEOUT: '채점이 시간 안에 끝나지 않았어요',
  LLM_ERROR: '채점 중 오류가 났어요',
  LLM_INVALID_OUTPUT: '채점 중 오류가 났어요',
  LLM_UNAVAILABLE: '채점 중 오류가 났어요',
  AI_TIMEOUT: '분석이 시간 안에 끝나지 않았어요',
  AI_UNAVAILABLE: '분석 서버에 연결하지 못했어요',
  AI_ERROR: '분석 중 오류가 났어요',
  AI_REJECTED: '이 Take는 대본 전달도를 계산할 수 없어요',
  AI_INVALID_RESPONSE: '분석 결과를 읽지 못했어요',
  LEGACY_RESULT: '이 Take는 자세한 결과를 지원하기 전에 분석됐어요',
  NO_RESULT: '대본 전달도 결과가 없어요',
  TAKE_FAILED: '발표가 정상적으로 끝나지 않아 분석하지 못했어요',
};

export const UNKNOWN_REASON = '이 값을 계산하지 못했어요';

/**
 * 문구가 어느 자리에 서는가.
 * - `slide`: 장 하나 (장별 목록 · 펼침) — 문구표 그대로
 * - `failedSlide`: 평가하지 못한 장
 * - `take`: Take 전체 (카드 위쪽 숫자 · 수치 · 말의 구성). #1152 에서 Take 지표는 장들의 이유 코드를
 *   합쳐 받으므로(`STT_INCOMPLETE` · `SENTENCE_FALLBACK`), "이 장" 문구를 쓰면 틀립니다
 */
export type ReasonScope = 'slide' | 'failedSlide' | 'take';

/** 장 전체가 평가되지 못했을 때는 같은 코드라도 다르게 말합니다 */
const FAILED_SLIDE_REASON: Record<string, string> = {
  STT_INCOMPLETE: '음성 인식이 끊겨 이 장을 평가하지 못했어요',
};

/** Take 전체 자리 — 문구표의 "이 장" 문구만 바꿉니다 */
const TAKE_REASON: Record<string, string> = {
  NO_KEY_POINTS: '확인할 핵심 내용이 없어요',
  NO_SPEECH: '말한 내용이 없어요',
  SENTENCE_FALLBACK: '일부 장은 문장 단위로 평가했어요(참고용)',
  STT_INCOMPLETE: '음성 인식이 끊긴 장이 있어요. 녹음된 부분만 봤어요',
};

const SCOPED_REASON: Record<ReasonScope, Record<string, string>> = {
  slide: {},
  failedSlide: FAILED_SLIDE_REASON,
  take: TAKE_REASON,
};

export function reasonText(code: string | null, scope: ReasonScope = 'slide'): string {
  if (code === null) return UNKNOWN_REASON;
  const scoped = SCOPED_REASON[scope];
  if (Object.hasOwn(scoped, code)) return scoped[code]!;
  return Object.hasOwn(REASON, code) ? REASON[code]! : UNKNOWN_REASON;
}

/** 같은 문구는 한 번만 — `NOT_VALIDATED` · `OFFSCRIPT_DISABLED` 처럼 문구가 겹치는 코드가 있습니다 */
export function reasonTexts(
  codes: readonly string[],
  skip: readonly string[] = [],
  scope: ReasonScope = 'slide',
): string[] {
  return [...new Set(codes.filter((c) => !skip.includes(c)).map((c) => reasonText(c, scope)))];
}

/** 값이 null 인 지표 자리에 보일 이유. `SLIDES_EXCLUDED` 는 범위 문구가 따로 있어 뒤로 미룹니다 */
export function metricReason(metric: Metric | null, scope: ReasonScope = 'slide'): string {
  if (!metric) return UNKNOWN_REASON;
  const own = metric.reason_codes.filter((c) => c !== 'SLIDES_EXCLUDED');
  return reasonText(own[0] ?? metric.reason_codes[0] ?? null, scope);
}

/** 0~1 → 정수 %. null 은 0 이 아니라 null 로 돌려줍니다 */
export function percent(value: number | null): number | null {
  return value === null ? null : Math.round(value * 100);
}

export function percentText(value: number | null): string {
  const p = percent(value);
  return p === null ? '—' : `${p}%`;
}

export const COVERAGE_STATUS_LABEL: Record<CoverageStatus, string> = {
  covered: '전달',
  partial: '일부',
  missing: '빠뜨림',
  contradicted: '다르게 말함',
};

export const SENTENCE_STATUS_LABEL: Record<SentenceStatus, string> = {
  said: '전달',
  partial: '일부',
  missing: '빠뜨림',
  contradicted: '다르게 말함',
};

export function coverageStatusLabel(status: string): string {
  return Object.hasOwn(COVERAGE_STATUS_LABEL, status)
    ? COVERAGE_STATUS_LABEL[status as CoverageStatus]
    : '판정 보류';
}

export const MIX_LABEL: Record<MixCategory, string> = {
  script: '대본 내용',
  other_slide: '다른 장 내용',
  procedural: '진행 말',
  off_script: '대본 밖 말',
  related_aside: '관련 곁말',
  unrelated: '무관한 말',
  undetermined: '판단 보류',
};

export function mixLabel(category: string): string {
  return Object.hasOwn(MIX_LABEL, category) ? MIX_LABEL[category as MixCategory] : '그 밖의 말';
}

const BAND_LABEL: Record<DeliveryBand, string> = {
  mostly: '대부분 말함',
  about_half: '절반쯤 말함',
  little: '거의 못 말함',
};

/** "대부분 말함". 간이 값이라는 표시는 화면이 '간이' 배지 하나로만 붙입니다(#1157 리뷰). 모르는 단계 · null 은 null */
export function bandText(band: string | null): string | null {
  if (band === null || !Object.hasOwn(BAND_LABEL, band)) return null;
  return BAND_LABEL[band as DeliveryBand];
}

const ZERO_KIND_TEXT: Record<ZeroKind, string> = {
  slide_skipped: '말한 내용이 없었어요',
  all_missing: '핵심 내용을 모두 빠뜨렸어요',
  missing_or_contradicted: '핵심 내용을 빠뜨리거나 다르게 말했어요',
};

/** 전달률이 0 일 때만 설명합니다 */
export function zeroText(delivery: Metric | null, kind: string | null): string | null {
  if (delivery?.value !== 0 || kind === null || !Object.hasOwn(ZERO_KIND_TEXT, kind)) return null;
  return ZERO_KIND_TEXT[kind as ZeroKind];
}

/** 받은 `state` 를 화면 갈래로. 모르는 상태도 오면 일반 안내로 받습니다 */
export function contentHeadline(status: ContentStatus | null): string | null {
  if (status === null) return '대본 전달도 상세 결과를 아직 제공하지 않아요';
  switch (status.state) {
    case 'ANALYZING':
      return '대본 전달도를 분석하고 있어요';
    case 'NOT_MEASURED':
    case 'FAILED':
      return reasonText(status.reason_code, 'take');
    case 'MEASURED':
      return status.summary ? null : reasonText('NO_RESULT');
    default:
      return UNKNOWN_REASON;
  }
}

/** "2장 중 1장 평가 · 1장 간이" */
export function scopeLine(scope: Scope): string {
  const parts = [`${scope.target_slides}장 중 ${scope.scored_slides}장 평가`];
  if (scope.estimated_slides > 0) parts.push(`${scope.estimated_slides}장 간이`);
  return parts.join(' · ');
}

/** 채점된 장 없이 간이 장만 있을 때 대표 숫자 자리 — "대부분 말함 2장 · 절반쯤 말함 1장" ('간이' 배지는 화면이 붙임) */
export function estimatedOnlyLine(slides: readonly SlideSummary[]): string | null {
  const counts = new Map<DeliveryBand, number>();
  for (const slide of slides) {
    const band = slide.delivery_band;
    if (slide.state !== 'estimated' || band === null || !Object.hasOwn(BAND_LABEL, band)) continue;
    counts.set(band, (counts.get(band) ?? 0) + 1);
  }
  if (counts.size === 0) return null;
  const order: DeliveryBand[] = ['mostly', 'about_half', 'little'];
  const parts = order.flatMap((b) =>
    counts.has(b) ? [`${BAND_LABEL[b]} ${counts.get(b)}장`] : [],
  );
  return parts.join(' · ');
}

export function isEstimatedOnly(summary: CoverageSummary): boolean {
  return summary.scope.scored_slides === 0 && summary.scope.estimated_slides > 0;
}

/** `criteria_version` 의 `+` 앞(기능 버전) */
export function featureVersion(criteria: string | null): string | null {
  if (!criteria) return null;
  return criteria.split('+')[0] ?? null;
}

export interface PreviousDelta {
  takeNumber: number;
  /** %p, 정수 */
  delta: number;
  /** 어느 한쪽에서 장이 빠졌으면 */
  partial: boolean;
}

/**
 * 지난 Take 대비. 기능 버전이 같고 양쪽 전달률이 모두 숫자일 때만 냅니다 (#1154 3절 9번).
 * BE 는 지난 Take 값을 그대로 넘겨 칸이 null 일 수 있습니다 — 그러면 비교하지 않습니다
 */
export function previousDelta(status: ContentStatus): PreviousDelta | null {
  const prev = status.previous;
  const now = status.summary?.delivery.value ?? null;
  const before = prev?.delivery?.value ?? null;
  if (!prev || now === null || before === null) return null;
  const mine = featureVersion(status.criteria_version);
  if (mine === null || mine !== featureVersion(prev.criteria_version)) return null;
  // 범위를 모르면 "일부 장 제외" 안내를 붙이지 않습니다
  const prevExcluded = prev.scope?.excluded.length ?? 0;
  return {
    takeNumber: prev.take_number,
    delta: Math.round((now - before) * 100),
    partial: prevExcluded > 0 || (status.summary?.scope.excluded.length ?? 0) > 0,
  };
}

export function signed(delta: number): string {
  return delta > 0 ? `+${delta}` : String(delta);
}

export function factTotal(facts: FactSummary): number {
  return facts.matched + facts.approximate + facts.mismatched + facts.missing + facts.pending;
}

export type FactsLine =
  { kind: 'none' } | { kind: 'estimate'; text: string } | { kind: 'measured'; text: string };

/**
 * 수치 정확도 한 줄 (#1154 3절 6번).
 * 수치 0개면 비율을 그리지 않고, 모두 확인 대기면 `accuracy_range.best` 를 '확인 전 추정'으로 보입니다
 */
export function factsLine(facts: FactSummary): FactsLine {
  if (factTotal(facts) === 0) return { kind: 'none' };
  const accuracy = facts.accuracy;
  if (accuracy.value === null) {
    if (facts.accuracy_range)
      return { kind: 'estimate', text: `확인 전 추정 ${percentText(facts.accuracy_range.best)}` };
    return { kind: 'measured', text: metricReason(accuracy, 'take') };
  }
  if (facts.pending > 0) {
    // 개수는 받은 칸을 그대로 씁니다 — 정확도는 어림을 0.5 로 세어 정확도 × 개수가 맞음 개수와 다릅니다 (#1157 리뷰)
    const checked = factTotal(facts) - facts.pending;
    const parts = [`확인한 ${checked}개 중 ${facts.matched}개 맞음`];
    if (facts.approximate > 0) parts.push(`${facts.approximate}개 어림`);
    parts.push(`${facts.pending}개 확인 대기`);
    return { kind: 'measured', text: parts.join(' · ') };
  }
  return { kind: 'measured', text: `정확도 ${percentText(accuracy.value)}` };
}

/** 말의 구성을 막대 대신 이유로 보일 상태 */
const MIX_HIDDEN: readonly string[] = ['not_applicable', 'failed', 'skipped_by_deadline'];

export function mixHidden(state: string): boolean {
  return MIX_HIDDEN.includes(state);
}

/** 검증 전 범주를 합쳐 보였다는 안내가 필요한가 */
export function mixMerged(codes: readonly string[]): boolean {
  return codes.includes('NOT_VALIDATED') || codes.includes('OFFSCRIPT_DISABLED');
}

/* ── 장별 펼침 (#1154 4절) ─────────────────────────────────────── */

const IMPORTANCE_LABEL: Record<Importance, string> = {
  critical: '핵심',
  high: '중요',
  normal: '보통',
};

export function importanceLabel(importance: string): string {
  return Object.hasOwn(IMPORTANCE_LABEL, importance)
    ? IMPORTANCE_LABEL[importance as Importance]
    : '보통';
}

/** 대본 문장 배지. 채점하지 않는 문장(`skip`)이나 상태가 없으면 "채점 안 함" */
export function sentenceStatusLabel(role: string, status: string | null): string {
  if (role === 'skip' || status === null) return '채점 안 함';
  return Object.hasOwn(SENTENCE_STATUS_LABEL, status)
    ? SENTENCE_STATUS_LABEL[status as SentenceStatus]
    : '판정 보류';
}

const EXPRESSION_LABEL: Record<string, string> = {
  verbatim_like: '대본대로',
  paraphrased: '바꿔 말함',
  undetermined: '구분 어려움',
};

export function expressionLabel(mode: string | null): string | null {
  if (mode === null || !Object.hasOwn(EXPRESSION_LABEL, mode)) return null;
  return EXPRESSION_LABEL[mode]!;
}

const FACT_STATUS_LABEL: Record<string, string> = {
  matched: '맞음',
  approximate: '어림',
  mismatched: '다른 값',
  missing: '빠뜨림',
  pending: '확인 대기',
};

export function factStatusLabel(status: string): string {
  return Object.hasOwn(FACT_STATUS_LABEL, status) ? FACT_STATUS_LABEL[status]! : '판정 보류';
}

export interface MarkPiece {
  text: string;
  /** 대본 밖 구간이면 그 구간 */
  span: OffscriptSpan | null;
}

/**
 * 말한 문장 하나를 대본 밖 구간으로 나눕니다. 위치는 코드 포인트(`[start, end)`)라
 * UTF-16 인 `slice` 대신 `Array.from` 으로 자릅니다 — 이모지 하나가 UTF-16 으로는 둘이라서입니다.
 * 겹치거나 문장 밖을 가리키는 구간은 버립니다
 */
export function splitMarks(sentence: string, spans: readonly OffscriptSpan[]): MarkPiece[] {
  const chars = Array.from(sentence);
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const pieces: MarkPiece[] = [];
  let at = 0;
  for (const span of sorted) {
    if (span.start < at || span.end <= span.start || span.end > chars.length) continue;
    if (span.start > at) pieces.push({ text: chars.slice(at, span.start).join(''), span: null });
    pieces.push({ text: chars.slice(span.start, span.end).join(''), span });
    at = span.end;
  }
  if (at < chars.length) pieces.push({ text: chars.slice(at).join(''), span: null });
  return pieces;
}

/** 장 안내에서 뺄 코드 — 범위(`SLIDES_EXCLUDED`)는 Take 쪽에, 세부 구분 안내는 말의 구성 쪽에 이미 있습니다 */
const SLIDE_NOTE_SKIP: readonly string[] = [
  'SLIDES_EXCLUDED',
  'NOT_VALIDATED',
  'OFFSCRIPT_DISABLED',
];

/**
 * 채점된 장 하나의 평가 한계 안내 (#1157 리뷰). `STT_INCOMPLETE` · `STT_UNVERIFIED` · `SENTENCE_FALLBACK` 처럼
 * 장을 잠정으로 만드는 이유를 장 문구로 모읍니다. 같은 문구는 한 번만, `shown` 에 이미 보인 문구는 뺍니다
 */
export function slideNotes(
  codes: readonly string[],
  rubricQuality: string | null,
  shown: readonly string[] = [],
): string[] {
  const all =
    rubricQuality === 'sentence_fallback' && !codes.includes('SENTENCE_FALLBACK')
      ? [...codes, 'SENTENCE_FALLBACK']
      : codes;
  return reasonTexts(all, SLIDE_NOTE_SKIP).filter((text) => !shown.includes(text));
}
