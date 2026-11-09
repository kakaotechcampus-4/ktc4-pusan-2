import type { CoverageSummary } from '@/types/report';

/**
 * 내용 전달 카드가 그리기 전에 Take 요약(`content_status.summary`)의 모양을 확인합니다 (#1157 리뷰 P1).
 *
 * BE(#1156)는 AI 의 새 `detail` 이 저장할 수 있는 JSON 인지까지만 보고 안쪽 필수 구조는 보지 않습니다.
 * 그래서 `summary: {}` 처럼 객체이지만 칸이 빈 응답도 올 수 있고, 그대로 그리면 카드가 렌더 중에 던집니다.
 * 카드가 실제로 읽는 칸만 확인해, 어긋나면 null 을 돌려 카드만 "보여 드리지 못함"으로 낮춥니다
 */
export function readCoverageSummary(v: unknown): CoverageSummary | null {
  if (!isObj(v)) return null;
  const ok =
    isMetric(v.delivery) &&
    isMetric(v.contradiction) &&
    isStrOrNull(v.zero_kind) &&
    isShares(v.status_shares) &&
    isArr(v.critical_gaps, isTakeGap) &&
    isFacts(v.facts) &&
    isExpression(v.expression) &&
    isSpeechMix(v.speech_mix) &&
    isScope(v.scope) &&
    isArr(v.slides, isSlide);
  return ok ? (v as unknown as CoverageSummary) : null;
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isStr = (v: unknown): v is string => typeof v === 'string';
const isStrOrNull = (v: unknown) => v === null || isStr(v);
const isArr = (v: unknown, each: (x: unknown) => boolean) => Array.isArray(v) && v.every(each);
const isStrArr = (v: unknown) => isArr(v, isStr);
const isNumArr = (v: unknown) => isArr(v, isNum);
const hasNums = (v: Obj, keys: readonly string[]) => keys.every((k) => isNum(v[k]));

function isMetric(v: unknown): boolean {
  return (
    isObj(v) &&
    (v.value === null || isNum(v.value)) &&
    isStr(v.state) &&
    isStrArr(v.reason_codes) &&
    hasNums(v, ['evaluated', 'excluded'])
  );
}

const isMetricOrNull = (v: unknown) => v === null || isMetric(v);

function isShares(v: unknown): boolean {
  return v === null || (isObj(v) && Object.values(v).every(isNum));
}

function isTakeGap(v: unknown): boolean {
  return (
    isObj(v) &&
    isNum(v.slide_number) &&
    isStr(v.content) &&
    isStr(v.importance) &&
    isStr(v.status) &&
    isNumArr(v.said_in_slides)
  );
}

function isFacts(v: unknown): boolean {
  if (!isObj(v)) return false;
  const range = v.accuracy_range;
  return (
    hasNums(v, ['matched', 'approximate', 'mismatched', 'missing', 'pending']) &&
    isMetric(v.accuracy) &&
    (range === null || (isObj(range) && hasNums(range, ['best', 'worst'])))
  );
}

function isExpression(v: unknown): boolean {
  return isObj(v) && hasNums(v, ['verbatim_like', 'paraphrased', 'undetermined']);
}

function isMixItem(v: unknown): boolean {
  return isObj(v) && isStr(v.category) && hasNums(v, ['tokens', 'share']);
}

/** 장 요약의 말의 구성(`state` · `reason_codes` · `display`)과 Take 요약의 것이 같은 칸을 읽습니다 */
function isSpeechMix(v: unknown): boolean {
  return isObj(v) && isStr(v.state) && isStrArr(v.reason_codes) && isArr(v.display, isMixItem);
}

function isScope(v: unknown): boolean {
  return (
    isObj(v) &&
    hasNums(v, ['target_slides', 'scored_slides', 'estimated_slides', 'provisional_slides']) &&
    isArr(v.excluded, (e) => isObj(e) && isNum(e.slide_number) && isStr(e.reason_code))
  );
}

/** 장 상태는 글자인지만 봅니다 — 모르는 상태 값은 카드가 일반 문구로 받습니다 */
function isSlide(v: unknown): boolean {
  return (
    isObj(v) &&
    isNum(v.slide_number) &&
    isStr(v.state) &&
    isStrArr(v.reason_codes) &&
    isMetricOrNull(v.delivery) &&
    isMetricOrNull(v.contradiction) &&
    isShares(v.status_shares) &&
    isNum(v.critical_gap_count) &&
    isStrOrNull(v.delivery_band) &&
    (v.speech_mix === null || isSpeechMix(v.speech_mix))
  );
}
