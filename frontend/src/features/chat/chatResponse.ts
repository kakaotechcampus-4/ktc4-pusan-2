import type {
  Assessment,
  Card,
  ChatMessage,
  ClaimKind,
  Evidence,
  ListedAssessment,
  PlanStep,
  RunEvent,
  TraceItem,
} from '@/types/chat';

/**
 * 응답 검사 — 서버가 준 답을 화면에 그려도 되는 모양으로 읽습니다 (`chat-agent.md` L3-22 · 23 · 24 · 28 · 42 · 46, 12-4 F4).
 *
 * BE 는 문장 · 근거 · 카드를 모양을 정하지 않은 채(`Any`) 넘기므로, 타입이 아니라 여기서 값을 봅니다.
 * 화면은 문장을 고치거나 숫자를 다시 계산하지 않고, 여기서 통과한 것만 그립니다.
 *
 * 걸렸을 때 두 갈래입니다.
 * - **칩만 버림** — 문장은 멀쩡한데 가리키는 근거 · 자료가 없거나 깨졌으면 그 칩만 만들지 않습니다(L3-24 "누락된 칩을 만들지 않는다").
 * - **계약 오류** — 문장 자체가 깨졌거나 모르는 종류(`kind`)면, 그 앞 문장은 두고 그 문장부터 뒤의 문장과
 *   새 행동을 부르는 카드를 막고 안내를 붙입니다(L3-24 · L3-42). 이미 일어난 결과(목표 저장됨 등)의 카드는 그대로 둡니다.
 *   안내 문구와 메시지 목록을 다시 읽을지는 `errorText` · `shouldRefetch` 가 정합니다.
 */

/** 실행 조회 · 메시지 목록 응답의 크기 상한 (L3-22). 넘으면 처리하지 않습니다 */
export const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;

// ── 값 검사 도구 ──────────────────────────────────────────────

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === 'string';
const isText = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '';
const isStrOrNull = (v: unknown) => v === null || isStr(v);
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isIntOrNull = (v: unknown) => v === null || isInt(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isNumOrNull = (v: unknown) => v === null || isNum(v);
const isBoolOrNull = (v: unknown) => v === null || typeof v === 'boolean';
const isStrList = (v: unknown): v is string[] => Array.isArray(v) && v.every(isStr);
const oneOf =
  <T extends string>(values: readonly T[]) =>
  (v: unknown): v is T =>
    typeof v === 'string' && (values as readonly string[]).includes(v);
const codePoints = (s: string) => [...s].length;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** 내부 이동은 형식이 맞는 ID 로만 만듭니다(L3-26 · L3-32) */
export const isUuid = (v: unknown): v is string => typeof v === 'string' && UUID.test(v);

const CLAIM_KINDS: readonly ClaimKind[] = [
  'RECORD',
  'GUIDE',
  'GENERAL',
  'ACTION_RESULT',
  'SYSTEM',
  'ASSESSMENT',
];
const isClaimKind = oneOf(CLAIM_KINDS);
const isOrigin = oneOf(['RECORD', 'SCREEN', 'COMPUTED'] as const);
const isVerdict = oneOf(['SUPPORTED', 'PARTLY', 'CONTRADICTED', 'UNVERIFIABLE'] as const);
const isAssessmentType = oneOf(['MEASURED', 'PREFERENCE', 'KNOWLEDGE', 'REQUEST'] as const);
const isPlanStatus = oneOf(['TODO', 'DOING', 'DONE', 'SKIPPED'] as const);
const isOutcome = oneOf(['OK', 'EMPTY', 'ERROR', 'CACHED'] as const);

// ── 진행 (L3-22) ─────────────────────────────────────────────

/** 깨진 항목은 빼고 나머지를 보입니다. 뺀 것이 있으면 "일부 진행 정보를 표시하지 못했어요." */
export interface Readable<T> {
  items: T[];
  dropped: boolean;
}

const isPlanStep = (v: unknown): v is PlanStep =>
  isObj(v) && isText(v.step) && isPlanStatus(v.status) && isStrOrNull(v.note ?? null);

/** 할 일 목록 — null 은 아직 진행이 없는 것(BE)이라 빠진 것이 아닙니다 */
export function readPlan(v: unknown): Readable<PlanStep> {
  if (v === null || v === undefined) return { items: [], dropped: false };
  if (!Array.isArray(v)) return { items: [], dropped: true };
  const items = v.filter(isPlanStep).map((s) => ({ ...s, note: s.note ?? null }));
  return { items, dropped: items.length !== v.length };
}

const isTraceItem = (v: unknown): v is TraceItem =>
  isObj(v) &&
  isInt(v.seq) &&
  isStr(v.tool) &&
  isStr(v.args_summary) &&
  isStr(v.result_summary) &&
  isNum(v.ms) &&
  isOutcome(v.outcome);

/** 확인한 것(도구 기록) — 같은 순번은 처음 것만(반복 이벤트), 순번순 */
export function readTrace(v: unknown): Readable<TraceItem> {
  if (v === null || v === undefined) return { items: [], dropped: false };
  if (!Array.isArray(v)) return { items: [], dropped: true };
  const seen = new Set<number>();
  const items: TraceItem[] = [];
  for (const item of v) {
    if (!isTraceItem(item) || seen.has(item.seq)) continue;
    seen.add(item.seq);
    items.push(item);
  }
  items.sort((a, b) => a.seq - b.seq);
  return { items, dropped: items.length !== v.length };
}

// ── 근거 · 자료 · 판단 ─────────────────────────────────────────

/** 자료 칩에 쓸 값 — 출처 칸이 빠졌으면 null 이고 화면은 "정보 없음" 으로 보입니다(12-4) */
export interface SourceInfo {
  source_id: string;
  title: string;
  quote: string | null;
  source: string | null;
  license: string | null;
}

function readEvidence(v: unknown): Evidence | null {
  if (!isObj(v)) return null;
  const ok =
    isText(v.evidence_id) &&
    isOrigin(v.origin) &&
    isStrOrNull(v.take_id ?? null) &&
    isIntOrNull(v.take_number ?? null) &&
    isIntOrNull(v.slide_number ?? null) &&
    isBoolOrNull(v.saved ?? null) &&
    (v.inputs === null || v.inputs === undefined || isStrList(v.inputs)) &&
    (isNum(v.value) || isStr(v.value));
  if (!ok) return null;
  return {
    ...(v as unknown as Evidence),
    take_id: (v.take_id as string | null) ?? null,
    take_number: (v.take_number as number | null) ?? null,
    slide_number: (v.slide_number as number | null) ?? null,
    saved: (v.saved as boolean | null) ?? null,
    inputs: (v.inputs as string[] | null) ?? null,
  };
}

const textOrNull = (v: unknown) => (isText(v) ? v : null);

function readSource(v: unknown): SourceInfo | null {
  if (!isObj(v) || !isText(v.source_id) || !isText(v.title)) return null;
  return {
    source_id: v.source_id,
    title: v.title,
    quote: textOrNull(v.quote),
    source: textOrNull(v.source),
    license: textOrNull(v.license),
  };
}

function readAssessment(v: unknown): ListedAssessment | null {
  if (!isObj(v)) return null;
  const ok =
    isText(v.assessment_id) &&
    isStr(v.quote) &&
    isAssessmentType(v.type) &&
    isVerdict(v.verdict) &&
    isStrList(v.evidence_ids) &&
    isStrList(v.source_ids) &&
    isStr(v.reason) &&
    isStrList(v.supersedes) &&
    isStrOrNull(v.superseded_by ?? null);
  if (!ok) return null;
  return {
    ...(v as unknown as Assessment),
    superseded_by: (v.superseded_by as string | null) ?? null,
  };
}

/** 한 답(메시지) · 한 실행이 받은 근거 · 자료 · 판단 — 칩과 판정은 여기서 찾습니다 */
export interface Basis {
  evidence: Map<string, Evidence>;
  sources: Map<string, SourceInfo>;
  assessments: Map<string, ListedAssessment>;
}

export const emptyBasis = (): Basis => ({
  evidence: new Map(),
  sources: new Map(),
  assessments: new Map(),
});

/** 받은 근거 · 자료 · 판단을 더합니다 — 깨진 항목은 넣지 않아, 그것을 가리키는 칩은 만들어지지 않습니다 */
function addToBasis(basis: Basis, evidence: unknown, sources: unknown, assessments: unknown) {
  for (const e of Array.isArray(evidence) ? evidence : []) {
    const read = readEvidence(e);
    if (read && !basis.evidence.has(read.evidence_id)) basis.evidence.set(read.evidence_id, read);
  }
  for (const s of Array.isArray(sources) ? sources : []) {
    const read = readSource(s);
    if (read && !basis.sources.has(read.source_id)) basis.sources.set(read.source_id, read);
  }
  for (const a of Array.isArray(assessments) ? assessments : []) {
    const read = readAssessment(a);
    // 같은 판단이 다시 오면 나중 것(대체 표시가 붙었을 수 있음)으로
    if (read) basis.assessments.set(read.assessment_id, read);
  }
}

// ── 칩 (12-4 F4) ─────────────────────────────────────────────

/**
 * 근거 칩. 내 기록은 Take · 장으로, 화면 값은 링크 없이, 자료는 제목으로 그립니다.
 * `takeId` 는 형식이 맞을 때만 있고, 그때만 Take 리포트로 이동합니다
 */
export type Chip =
  | {
      type: 'RECORD';
      key: string;
      takeId: string | null;
      takeNumber: number | null;
      slideNumber: number | null;
    }
  | { type: 'SCREEN'; key: string; saved: boolean }
  | ({ type: 'SOURCE'; key: string } & SourceInfo);

/** 칩 글자 — "Take 5 · 3장" · "지금 화면의 입력값" · "자료: 제목" (12-4) */
export function chipLabel(chip: Chip): string {
  if (chip.type === 'SOURCE') return `자료: ${chip.title}`;
  if (chip.type === 'SCREEN') return chip.saved ? '저장된 값' : '지금 화면의 입력값';
  const parts = [
    chip.takeNumber === null ? null : `Take ${chip.takeNumber}`,
    chip.slideNumber === null ? null : `${chip.slideNumber}장`,
  ].filter((p): p is string => p !== null);
  return parts.length > 0 ? parts.join(' · ') : '내 기록';
}

/** 계산 근거는 입력을 따라가되, 서로 가리키는 고리 · 너무 깊은 사슬에서 멈춥니다 */
const MAX_INPUT_DEPTH = 4;

/** 문장 · 카드 · 판단이 가리키는 근거 · 자료로 칩을 만듭니다. 찾지 못한 참조 수는 `missing` */
export function chipsFor(
  refs: { evidence_ids: readonly string[]; source_ids?: readonly string[] },
  basis: Basis,
): { chips: Chip[]; missing: number } {
  const chips = new Map<string, Chip>();
  let missing = 0;

  const visit = (id: string, depth: number, seen: Set<string>) => {
    const e = basis.evidence.get(id);
    if (!e || seen.has(id) || depth > MAX_INPUT_DEPTH) {
      missing += 1;
      return;
    }
    seen.add(id);
    if (e.origin === 'COMPUTED') {
      // 차이 · 거리 · 목표 판정은 Take 가 없을 수 있어 입력 근거마다 그 종류대로 (12-4)
      if (!e.inputs || e.inputs.length === 0) missing += 1;
      for (const input of e.inputs ?? []) visit(input, depth + 1, new Set(seen));
      return;
    }
    if (e.origin === 'SCREEN') {
      const saved = e.saved === true;
      const key = `S:${saved}`;
      if (!chips.has(key)) chips.set(key, { type: 'SCREEN', key, saved });
      return;
    }
    const takeId = isUuid(e.take_id) ? e.take_id : null;
    // 같은 Take · 장은 하나로 (12-4)
    const key = `R:${takeId ?? e.take_number ?? '-'}:${e.slide_number ?? '-'}`;
    if (!chips.has(key))
      chips.set(key, {
        type: 'RECORD',
        key,
        takeId,
        takeNumber: e.take_number,
        slideNumber: e.slide_number,
      });
  };

  for (const id of refs.evidence_ids) visit(id, 0, new Set());
  for (const id of refs.source_ids ?? []) {
    const s = basis.sources.get(id);
    if (!s) {
      missing += 1;
      continue;
    }
    const key = `G:${s.source_id}`;
    if (!chips.has(key)) chips.set(key, { type: 'SOURCE', key, ...s });
  }
  return { chips: [...chips.values()], missing };
}

// ── 문장 (L3-24 · L3-42) ─────────────────────────────────────

/** 그려도 되는 문장 하나 */
export interface ShownClaim {
  /** 실행 전체에서 이어지는 순번. 메시지 목록에서는 그 답 안의 자리 */
  index: number;
  kind: ClaimKind;
  text: string;
  chips: Chip[];
  /** 근거 · 자료를 찾지 못해 만들지 않은 칩 수 */
  missing: number;
  /** 일반 조언 — 칩 없이 "일반 조언 · 기록과 자료로 확인한 것이 아니에요" 라벨 (L3-42) */
  general: boolean;
  /** 판단 문장의 판단. 받지 못했으면 null — 판정을 추정하지 않습니다(12-4) */
  assessment: ListedAssessment | null;
  actionId: string | null;
}

/** 계약 오류 사유 — 화면 문구는 모두 "답의 근거를 확인하지 못했으니 다시 물어봐 주세요." */
export type ContractError =
  /** 문장 모양이 틀림(빈 글 · 300자 초과 · 참조가 목록이 아님 등) */
  | 'BAD_CLAIM'
  /** 모르는 문장 종류 */
  | 'UNKNOWN_KIND'
  /** 문장 순번이 건너뜀 */
  | 'INDEX_GAP'
  /** 답 글이 문장들을 이은 값과 다름 */
  | 'TEXT_MISMATCH'
  /** 끝난 답인데 문장이 없음 (L3-23) */
  | 'EMPTY_ANSWER';

export const CONTRACT_ERROR_TEXT = '답의 근거를 확인하지 못했으니 다시 물어봐 주세요.';
/** 끝난 답이 비었거나 손상됨 (L3-23) */
export const ANSWER_FAILED_TEXT = '지금은 답을 만들지 못했어요.';

/** 오류 안내 — 답이 비었으면 L3-23, 그 밖의 검사 실패는 L3-24 · L3-42 문구 */
export const errorText = (error: ContractError) =>
  error === 'EMPTY_ANSWER' ? ANSWER_FAILED_TEXT : CONTRACT_ERROR_TEXT;

/**
 * 메시지 목록을 한 번 다시 읽을지 — 저장된 답 자체가 비었거나 글과 문장이 어긋난 손상일 때만(L3-23 "목록 재조회 1회").
 * 다시 읽어도 같으면 그대로 안내합니다. 몇 번 읽었는지는 화면이 셉니다
 */
export const shouldRefetch = (error: ContractError | null) =>
  error === 'EMPTY_ANSWER' || error === 'TEXT_MISMATCH';
export const PROGRESS_DROPPED_TEXT = '일부 진행 정보를 표시하지 못했어요.';
export const GENERAL_LABEL = '일반 조언 · 기록과 자료로 확인한 것이 아니에요';

function readClaim(v: unknown, index: number, basis: Basis): ShownClaim | { error: ContractError } {
  if (!isObj(v)) return { error: 'BAD_CLAIM' };
  if (!isClaimKind(v.kind)) return { error: 'UNKNOWN_KIND' };
  const { kind, text, evidence_ids, source_ids } = v;
  const ok =
    isText(text) &&
    codePoints(text) <= 300 &&
    isStrList(evidence_ids) &&
    isStrList(source_ids) &&
    isStrOrNull(v.assessment_id ?? null) &&
    isStrOrNull(v.action_id ?? null);
  if (!ok) return { error: 'BAD_CLAIM' };
  const general = kind === 'GENERAL';
  // 일반 조언에 근거가 실려 와도 칩을 만들지 않습니다 (L3-42)
  const { chips, missing } = general
    ? { chips: [], missing: 0 }
    : chipsFor({ evidence_ids, source_ids }, basis);
  const assessmentId = (v.assessment_id as string | null) ?? null;
  return {
    index,
    kind,
    text,
    chips,
    missing,
    general,
    assessment:
      kind === 'ASSESSMENT' && assessmentId !== null
        ? (basis.assessments.get(assessmentId) ?? null)
        : null,
    actionId: (v.action_id as string | null) ?? null,
  };
}

// ── 카드 (L3-28) ─────────────────────────────────────────────

/** 그려도 되는 카드, 또는 버튼 없는 대체 표시("이 카드를 표시할 수 없으니 새로고침해 주세요.") */
export type ShownCard =
  | {
      ok: true;
      card: Card;
      /** 카드의 근거 칩(목표 저장됨) */
      chips: Chip[];
      /** 목표 제안 안마다의 근거 칩 */
      optionChips: Chip[][];
    }
  | { ok: false; type: string | null };

export const UNSUPPORTED_CARD_TEXT = '이 카드를 표시할 수 없으니 새로고침해 주세요.';

const isOperator = oneOf(['gt_base', 'lt_base', 'in_range'] as const);
const isProfileKey = oneOf(['TONE', 'EXPLAIN_STYLE', 'FOCUS_AREA', 'CONTEXT'] as const);
/** 시각은 ISO 8601 만 — `Date.parse` 는 "1" 같은 값도 날짜로 받습니다 */
const isIsoTime = (v: unknown): v is string =>
  isStr(v) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v) && !Number.isNaN(Date.parse(v));

/** 목표 (5-2 `Goal` · 제안의 `GoalDraft`) — 화면이 그리는 지표 · 장 · 비교 방법 · 기준 */
const isGoal = (v: unknown) =>
  isObj(v) &&
  isText(v.metric) &&
  isOperator(v.operator) &&
  isIntOrNull(v.slide_number ?? null) &&
  isObj(v.base) &&
  isNum(v.base.value) &&
  isIntOrNull(v.base.take_number ?? null);

const isReassessSide = (v: unknown) =>
  isObj(v) &&
  isNumOrNull(v.score) &&
  isObj(v.dimensions) &&
  Object.values(v.dimensions).every((d) => d === null || isStr(d)) &&
  Array.isArray(v.criteria_results);

const isGoalOption = (v: unknown) =>
  isObj(v) &&
  oneOf(['USER', 'EXPERT'] as const)(v.label) &&
  (v.goal === null || isGoal(v.goal)) &&
  isStr(v.reason) &&
  isStrList(v.evidence_ids) &&
  isStrOrNull(v.content_hash ?? null);

/** 카드 종류마다 필수 칸 — 모르는 종류 · 빠진 칸은 대체 표시 */
const CARD_CHECKS: Record<Card['type'], (c: Obj) => boolean> = {
  GOAL_SAVED: (c) =>
    isText(c.action_id) &&
    isGoal(c.goal) &&
    isInt((c.goal as Obj).version) &&
    isStr(c.reason) &&
    isStrList(c.evidence_ids),
  GOAL_PROPOSAL: (c) =>
    isText(c.proposal_id) &&
    Array.isArray(c.options) &&
    c.options.length >= 1 &&
    c.options.length <= 2 &&
    c.options.every(isGoalOption),
  // 한쪽이라도 없으면 부분 비교하지 않습니다 (L3-33)
  REASSESS_RESULT: (c) =>
    isText(c.take_id) &&
    isReassessSide(c.before) &&
    isReassessSide(c.after) &&
    isStrList(c.changed_fields) &&
    typeof c.official_differs === 'boolean',
  // 고정 경로로만 이동하므로 ID 형식부터 봅니다 (L3-32)
  START_PRACTICE: (c) => isUuid(c.pitch_id),
  ASK_CONFIRM: (c) =>
    isText(c.action_id) &&
    oneOf(['SAVE_GOAL', 'REASSESS'] as const)(c.action_type) &&
    isText(c.title) &&
    isStr(c.summary) &&
    (c.before === null || isObj(c.before)) &&
    isObj(c.after) &&
    isStrList(c.notes) &&
    isIsoTime(c.expires_at) &&
    isText(c.args_hash),
  GOAL_RESULT: (c) =>
    isInt(c.goal_version) &&
    isText(c.take_id) &&
    oneOf(['ACHIEVED', 'NOT_ACHIEVED', 'UNKNOWN', 'BASELINE_MISMATCH'] as const)(c.status) &&
    isNumOrNull(c.observed) &&
    isStr(c.reason),
  MEMORY_SAVED: (c) => isText(c.item_id) && isProfileKey(c.key) && isStr(c.value),
};

const isCardType = (v: unknown): v is Card['type'] =>
  typeof v === 'string' && Object.hasOwn(CARD_CHECKS, v);

/** 카드 하나 — 다른 카드가 깨져도 이 카드는 그대로 (L3-28 "다른 유효 카드는 유지") */
export function readCard(v: unknown, basis: Basis): ShownCard {
  if (!isObj(v)) return { ok: false, type: null };
  if (!isCardType(v.type) || !CARD_CHECKS[v.type](v))
    return { ok: false, type: isStr(v.type) ? v.type : null };
  const card = v as unknown as Card;
  const chips = card.type === 'GOAL_SAVED' ? chipsFor(card, basis).chips : [];
  const optionChips =
    card.type === 'GOAL_PROPOSAL' ? card.options.map((o) => chipsFor(o, basis).chips) : [];
  return { ok: true, card, chips, optionChips };
}

/**
 * 새 행동을 부르는 카드 — 계약 오류 뒤에는 막습니다("오류 뒤의 출력 · 행동만 막는다").
 * 목표 저장됨 · 목표 확인 결과 · 참고 결과 · 기억했어요는 이미 일어난 결과라 그대로 보여, [되돌리기]도 남깁니다
 */
const ACTION_CARDS: readonly string[] = ['ASK_CONFIRM', 'GOAL_PROPOSAL', 'START_PRACTICE'];

/** 계약 오류 뒤에 남길 카드 — 읽을 수 있는 결과 카드만 */
const afterError = (cards: ShownCard[]) =>
  cards.filter((c) => c.ok && !ACTION_CARDS.includes(c.card.type));

export function readCards(v: unknown, basis: Basis): ShownCard[] {
  if (v === null || v === undefined) return [];
  if (!Array.isArray(v)) return [{ ok: false, type: null }];
  return v.map((c) => readCard(c, basis));
}

// ── 메시지 목록의 답 ──────────────────────────────────────────

/** 그려도 되는 답 */
export interface ShownAnswer {
  claims: ShownClaim[];
  /** 계약 오류면 새 행동을 부르는 카드는 빠지고 결과 카드만 남습니다 */
  cards: ShownCard[];
  error: ContractError | null;
  basis: Basis;
}

/**
 * 메시지 목록의 답 하나를 읽습니다. 근거 · 자료 · 판단은 그 메시지가 실은 것에서 찾습니다(12-3 메시지 목록).
 * 사용자 · 시스템 메시지와 가린 메시지(`masked`)는 문장이 없어 글(`text`)을 그대로 보입니다
 */
export function readMessage(message: ChatMessage): ShownAnswer {
  const basis = emptyBasis();
  if (message.role !== 'assistant' || message.masked)
    return { claims: [], cards: [], error: null, basis };
  addToBasis(basis, message.evidence, message.sources, message.assessments);

  const raw: unknown = message.claims;
  const list = Array.isArray(raw) ? raw : [];
  const claims: ShownClaim[] = [];
  let error: ContractError | null = raw === null || Array.isArray(raw) ? null : 'BAD_CLAIM';
  for (const [i, item] of list.entries()) {
    if (error) break;
    const read = readClaim(item, i, basis);
    if ('error' in read) error = read.error;
    else claims.push(read);
  }
  // 끝난 답에 문장이 없음 · 글과 문장이 다름 — 손상으로 보고 목록을 한 번 다시 읽게 합니다 (L3-23 · L3-24)
  if (!error && list.length === 0 && message.status === 'DONE') error = 'EMPTY_ANSWER';
  if (!error && list.length > 0 && message.text !== claims.map((c) => c.text).join('\n'))
    error = 'TEXT_MISMATCH';
  const cards = readCards(message.cards, basis);
  return { claims, cards: error ? afterError(cards) : cards, error, basis };
}

// ── 실행 중에 받는 문장 줄 (L3-46) ─────────────────────────────

/**
 * 한 실행의 문장 줄을 차례로 읽습니다. 근거 · 판단은 실행 전체에서 찾습니다 — 앞 줄이 실어 온 근거를
 * 뒤 문장 · 카드가 가리킬 수 있습니다. 같은 `index` 는 한 번만, 건너뛰면 계약 오류로 멈춥니다
 */
export interface RunReader {
  /** 새로 그릴 문장 — 이미 받은 것 · 오류 뒤의 것은 빠집니다 */
  add: (events: readonly RunEvent[]) => ShownClaim[];
  /** 대기 카드 — 계약 오류 뒤에는 새 행동을 부르는 카드를 막습니다 */
  cards: (v: unknown) => ShownCard[];
  readonly error: ContractError | null;
  readonly basis: Basis;
}

export function createRunReader(): RunReader {
  const basis = emptyBasis();
  let expected = 0;
  let error: ContractError | null = null;
  return {
    add(events) {
      const shown: ShownClaim[] = [];
      for (const event of events) {
        if (error) break;
        if (event.event !== 'claim') continue;
        const data: unknown = event.data;
        if (!isObj(data) || !isInt(data.index)) {
          error = 'BAD_CLAIM';
          break;
        }
        if (data.index < expected) continue;
        if (data.index > expected) {
          error = 'INDEX_GAP';
          break;
        }
        addToBasis(basis, data.evidence, data.sources, data.assessments);
        const read = readClaim(data.claim, data.index, basis);
        if ('error' in read) {
          error = read.error;
          break;
        }
        expected += 1;
        shown.push(read);
      }
      return shown;
    },
    cards: (v) => (error ? afterError(readCards(v, basis)) : readCards(v, basis)),
    get error() {
      return error;
    },
    basis,
  };
}
