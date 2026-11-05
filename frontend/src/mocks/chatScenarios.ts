import { homeFixture } from './home';
import type {
  Assessment,
  Card,
  ChatScreen,
  ClaimEventData,
  Evidence,
  Goal,
  GuideCard,
  PlanStep,
  ProposedGoal,
  RunStatus,
  TraceItem,
} from '@/types/chat';

/**
 * 대화 에이전트 목의 고정 자료와 실행 시나리오 — 요청 처리는 `mocks/chat.ts` 입니다.
 * 시나리오 목록과 고르는 순서는 `mocks/chat.ts` 머리말에 있습니다.
 */

export const pitch = homeFixture.pitches[0]!;
export const PITCH_ID = pitch.pitch_id;
export const [take1, take2, take3] = pitch.takes;

// ── 고정 자료 ─────────────────────────────────────────────────

export const evidence = (
  e: Partial<Evidence> & Pick<Evidence, 'evidence_id' | 'path' | 'value'>,
): Evidence => ({
  origin: 'RECORD',
  pitch_id: PITCH_ID,
  take_id: null,
  take_number: null,
  kind: 'TAKE_RESULT',
  revision: 1,
  metric: null,
  slide_number: null,
  unit: null,
  rule_version: 'gaze-1.3',
  saved: null,
  screen_snapshot_id: null,
  inputs: null,
  calc: null,
  ...e,
});

const gazeOf = (take: typeof take2, value: number, id: string) =>
  evidence({
    evidence_id: id,
    take_id: take!.take_id,
    take_number: take!.take_version,
    path: 'take_result.areas.GAZE.slides[slide_number=3].metrics.audience_ratio',
    metric: 'audience_ratio',
    slide_number: 3,
    value,
  });

const guide: GuideCard = {
  source_id: 'g-07#2',
  title: '대본 의존 줄이기',
  quote: '문장 전체가 아니라 장마다 첫 낱말만 보고 말을 이어 가면 청중을 보는 시간이 늘어요.',
  source: '피치코치 발표법 자료',
  license: 'CC BY 4.0',
};

const usageGuide: GuideCard = {
  source_id: 'u-03#1',
  title: '발표 정보 바꾸기',
  quote: '제한 시간은 피치 만들기의 발표 정보 단계에서 바꾸고, 저장하면 다음 연습부터 적용돼요.',
  source: '피치코치 사용법',
  license: '피치코치',
};

const practiceGuide: GuideCard = {
  source_id: 'u-05#1',
  title: '연습 시작하기',
  quote: '연습 준비 화면에서 대본 보기 방식을 고르고 카메라 · 마이크를 확인한 뒤 시작해요.',
  source: '피치코치 사용법',
  license: '피치코치',
};

export type ClaimStep = Omit<ClaimEventData, 'index'>;

export const say = (
  kind: ClaimStep['claim']['kind'],
  text: string,
  extra: Partial<ClaimStep['claim']> = {},
  rest: Partial<Omit<ClaimStep, 'claim'>> = {},
): ClaimStep => ({
  claim: {
    kind,
    text,
    assessment_id: null,
    evidence_ids: [],
    source_ids: [],
    action_id: null,
    ...extra,
  },
  evidence: [],
  sources: [],
  assessments: [],
  ...rest,
});

export const percent = (ratio: number) => Math.round(ratio * 100);

/** 두 Take 의 3장 청중 보기 비율 비교. 기본은 최근 완료 Take 둘(2 → 3) */
export function compareClaims(
  from = take2,
  to = take3,
  [fromValue, toValue]: [number, number] = [0.31, 0.24],
  prefix = 'ev',
): ClaimStep[] {
  const a = gazeOf(from, fromValue, `${prefix}-gaze-t${from!.take_version}`);
  const b = gazeOf(to, toValue, `${prefix}-gaze-t${to!.take_version}`);
  const delta = Math.round((toValue - fromValue) * 100) / 100;
  const diff = evidence({
    evidence_id: `${prefix}-gaze-diff`,
    origin: 'COMPUTED',
    kind: null,
    revision: null,
    path: 'computed.diff(audience_ratio, slide 3)',
    metric: 'audience_ratio',
    slide_number: 3,
    value: delta,
    inputs: [a.evidence_id, b.evidence_id],
    calc: 'DIFF',
  });
  return [
    say(
      'RECORD',
      `3장에서 청중을 본 비율이 Take ${from!.take_version} ${percent(fromValue)}%에서 Take ${to!.take_version} ${percent(toValue)}%로 ${Math.abs(percent(delta))}%p ${delta < 0 ? '줄었어요' : '늘었어요'}.`,
      { evidence_ids: [diff.evidence_id] },
      { evidence: [a, b, diff] },
    ),
    say(
      'GUIDE',
      '장마다 첫 낱말만 보고 말을 이어 가 보세요.',
      { source_ids: [guide.source_id] },
      { sources: [guide] },
    ),
    // 일반 조언에는 숫자 · 한글 수 · 횟수를 쓰지 않습니다 (6-9)
    say('GENERAL', '말하기 전에 숨을 고르고 청중 쪽을 먼저 보면 시작이 안정돼요.'),
  ];
}

const plan = (statuses: PlanStep['status'][], steps: string[]): PlanStep[] =>
  steps.map((step, i) => ({ step, status: statuses[i] ?? 'TODO', note: null }));

const trace = (seq: number, tool: string, args: string, result: string): TraceItem => ({
  seq,
  tool,
  args_summary: args,
  result_summary: result,
  ms: 120,
  outcome: 'OK',
});

function savedGoal(version: number): Goal {
  return {
    version,
    pitch_id: PITCH_ID,
    created_at: new Date().toISOString(),
    metric: 'audience_ratio',
    slide_number: 3,
    operator: 'gt_base',
    script_version_id: 'sv2',
    standard_version: 4,
    goal_rule_version: 'chat_goal-1',
    base: {
      take_id: take3!.take_id,
      take_number: take3!.take_version,
      kind: 'TAKE_RESULT',
      revision: 1,
      value: 0.24,
      metric_rule_version: 'gaze-1.3',
      range: null,
    },
    min_delta: 0.05,
    status: 'OPEN',
  };
}

// ── 실행 시나리오 ─────────────────────────────────────────────

export type Step =
  | { t: 'plan'; plan: PlanStep[] }
  | { t: 'tool'; trace: TraceItem }
  | { t: 'claim'; data: ClaimStep }
  | {
      t: 'ask';
      action_type: 'SAVE_GOAL' | 'REASSESS';
      title: string;
      summary: string;
      before: Record<string, unknown> | null;
      after: Record<string, unknown>;
      notes: string[];
      approved: Step[];
      rejected: string;
    }
  | { t: 'end'; status: RunStatus; cards: (actionId: string) => Card[] };

const STEPS = ['최근 Take 비교', '3장 문제 구간 확인', '발표법 자료 찾기'];

/**
 * 비교 → 자료 찾기 → 할 일 모두 끝. 자료 문장은 그 턴에 자료를 찾은 뒤에만 나옵니다(6-9 · 6-10) —
 * 그래서 문장보다 앞에 자료 찾기 도구 기록을 둡니다
 */
const base = (): Step[] => [
  { t: 'plan', plan: plan(['DOING'], STEPS) },
  {
    t: 'tool',
    trace: trace(
      1,
      'compare_takes',
      `Take ${take2!.take_version} · ${take3!.take_version}`,
      '3장 시선 둘 다 낮음',
    ),
  },
  { t: 'plan', plan: plan(['DONE', 'DONE', 'DOING'], STEPS) },
  { t: 'tool', trace: trace(2, 'search_guides', '대본 의존', "자료 '대본 의존 줄이기'") },
  { t: 'plan', plan: plan(['DONE', 'DONE', 'DONE'], STEPS) },
];

/** 사용법만 찾는 짧은 턴 — 할 일 하나 */
const guideOnly = (query: string, found: string, outcome: TraceItem['outcome'] = 'OK'): Step[] => [
  { t: 'plan', plan: plan(['DOING'], ['사용법 자료 찾기']) },
  { t: 'tool', trace: { ...trace(1, 'search_guides', query, found), outcome } },
  { t: 'plan', plan: plan(['DONE'], ['사용법 자료 찾기']) },
];

/** 목표 제안 카드의 안 하나 */
export type GoalOption = Extract<Card, { type: 'GOAL_PROPOSAL' }>['options'][number];

export interface ScenarioContext {
  memoryOn: boolean;
  /** 제안 카드에서 고른 안. 없으면 null */
  chosen: GoalOption | null;
  screen: ChatScreen | null;
  /** 이번 판단에 붙일 ID 와, 같은 대화의 가장 최근 판단 */
  assessmentId: string;
  priorAssessmentId: string | null;
}

/**
 * 사용자 주장 판정 (6-3). 측정 주장은 코드가 판정하므로 `verdict` 는 기록대로 "기록과 다름" 입니다.
 * 실제로는 대상 Take 나 근거가 바뀌었을 때만 앞 판단을 대체합니다. 목은 대체 표시(`superseded_by`)를
 * 볼 수 있게, 같은 대화에서 다시 물으면 늘 앞 판단을 대체합니다.
 */
function assessmentSteps(text: string, ctx: ScenarioContext): Step[] {
  const [record, guideClaim] = compareClaims();
  // 사용자 말 원문의 부분 문자열 — 문장이 300자를 넘지 않게 "봤지" 앞 20자 안에서 자릅니다
  const quote = [...text.slice(0, text.indexOf('봤지') + 2)].slice(-20).join('').trim();
  const assessment: Assessment = {
    assessment_id: ctx.assessmentId,
    quote,
    type: 'MEASURED',
    verdict: 'CONTRADICTED',
    evidence_ids: record!.claim.evidence_ids,
    source_ids: [],
    reason: '3장 청중 보기 비율이 줄었어요.',
    supersedes: ctx.priorAssessmentId ? [ctx.priorAssessmentId] : [],
  };
  return [
    ...base(),
    {
      t: 'claim',
      data: say(
        'ASSESSMENT',
        `말씀하신 '${quote}'는 기록과 달라요. ${record!.claim.text}`,
        { assessment_id: ctx.assessmentId, evidence_ids: record!.claim.evidence_ids },
        { evidence: record!.evidence, assessments: [assessment] },
      ),
    },
    { t: 'claim', data: guideClaim! },
    { t: 'end', status: 'DONE', cards: () => [] },
  ];
}

/** 피치 만들기 · 수정 화면의 질문 — 화면 값을 근거로 쓰고 입력란은 바꾸지 않습니다 (12-4 Screen) */
function screenSteps(screen: Extract<ChatScreen, { name: 'pitch_create' | 'pitch_edit' }>): Step[] {
  const sec = screen.draft.time_limit_sec;
  const steps: Step[] = guideOnly('제한 시간 바꾸기', "자료 '발표 정보 바꾸기'");
  if (sec !== null) {
    const value = evidence({
      evidence_id: 'ev-screen-time',
      // 새 피치 화면은 아직 피치가 없습니다 — AI 코드는 null 을 허용합니다 (문서 5-2 표에는 아직 없음)
      pitch_id: screen.name === 'pitch_create' ? null : PITCH_ID,
      origin: 'SCREEN',
      kind: null,
      revision: null,
      rule_version: null,
      path: 'screen.draft.time_limit_sec',
      value: sec,
      unit: 'sec',
      // 저장값이 없으면(새 피치) `changed` 가 비어도 저장된 값이 아닙니다 (12-4 "저장값이 없으면 changed:[]")
      saved: screen.saved !== null && !screen.changed.includes('time_limit_sec'),
      screen_snapshot_id: screen.snapshot_id,
    });
    steps.push({
      t: 'claim',
      data: say(
        'RECORD',
        `지금 화면의 제한 시간은 ${Math.round(sec / 60)}분이에요.`,
        { evidence_ids: [value.evidence_id] },
        { evidence: [value] },
      ),
    });
  }
  steps.push(
    {
      t: 'claim',
      data: say(
        'GUIDE',
        '발표 정보 단계에서 바꾸고 저장하면 다음 연습부터 적용돼요.',
        { source_ids: [usageGuide.source_id] },
        { sources: [usageGuide] },
      ),
    },
    { t: 'end', status: 'DONE', cards: () => [] },
  );
  return steps;
}

export function scenario(text: string, ctx: ScenarioContext): Step[] {
  const [first, ...more] = compareClaims();
  // 고르기: 목표 카드가 가리키는 근거(3장 비교)를 같은 답 메시지에 싣습니다 — 메시지는 참조를 풀 근거를 함께 갖습니다
  if (ctx.chosen) {
    const chosen = ctx.chosen;
    return [
      // 근거를 가리키는 안이면 그 근거 문장을 같은 답에 싣습니다 — 메시지는 참조를 풀 근거를 함께 갖습니다
      ...(chosen.evidence_ids.length > 0 ? [{ t: 'claim', data: first! } as Step] : []),
      { t: 'claim', data: say('ACTION_RESULT', '목표를 저장했어요.') },
      {
        t: 'end',
        status: 'DONE',
        cards: (id) => [goalSavedCard(id, chosen)],
      },
    ];
  }
  if (text.includes('날씨'))
    return [
      { t: 'claim', data: say('SYSTEM', '발표 연습과 관계없는 질문이에요.') },
      { t: 'end', status: 'REFUSED', cards: () => [] },
    ];
  if (text.includes('제안')) {
    // "자신감 있게" 처럼 기록으로 잴 수 없는 사용자 안은 목표 없이(goal null) 설명만 둡니다 (12-4)
    const measurable = !text.includes('자신감');
    return [
      ...base(),
      { t: 'claim', data: first! },
      {
        t: 'end',
        status: 'DONE',
        cards: () => {
          const proposed = proposedGoal(savedGoal(0));
          return [
            {
              type: 'GOAL_PROPOSAL',
              proposal_id: 'proposal-1',
              options: [
                measurable
                  ? {
                      label: 'USER',
                      // 장이 없는 목표라 기준도 전체 장 평균(35%)입니다
                      goal: {
                        ...proposed,
                        slide_number: null,
                        base: { ...proposed.base, value: 0.35 },
                      },
                      reason: '말씀하신 대로 전체 장의 청중 보기를 올리는 목표예요.',
                      evidence_ids: [],
                      content_hash: 'hash-user',
                    }
                  : {
                      label: 'USER',
                      goal: null,
                      reason: '말씀하신 자신감은 기록으로 잴 수 없어 목표로 저장하지 않아요.',
                      evidence_ids: [],
                      content_hash: null,
                    },
                {
                  label: 'EXPERT',
                  goal: proposed,
                  reason: '3장 청중 보기 비율이 가장 낮아요.',
                  evidence_ids: ['ev-gaze-diff'],
                  content_hash: 'hash-expert',
                },
              ],
            },
          ];
        },
      },
    ];
  }
  if (text.includes('목표'))
    return [
      ...base(),
      { t: 'claim', data: first! },
      {
        t: 'ask',
        action_type: 'SAVE_GOAL',
        title: '대화 목표를 저장할까요?',
        summary: `3장 청중 보기 비율을 Take ${take3!.take_version}의 24%보다 5%p 넘게 올리기`,
        before: null,
        after: {
          metric: 'audience_ratio',
          slide_number: 3,
          operator: 'gt_base',
          base: 0.24,
          min_delta: 0.05,
        },
        notes: [],
        approved: [
          { t: 'claim', data: say('ACTION_RESULT', '목표를 저장했어요.') },
          { t: 'end', status: 'DONE', cards: (id) => [goalSavedCard(id)] },
        ],
        rejected: '목표를 저장하지 않았어요.',
      },
    ];
  if (text.includes('다시 평가') || text.includes('대본 보고'))
    return [
      ...base(),
      { t: 'claim', data: say('SYSTEM', '실제로 대본을 봤는지는 기록으로 확인할 수 없어요.') },
      {
        t: 'ask',
        action_type: 'REASSESS',
        title: '대본을 보고 한 발표로 다시 평가할까요?',
        summary: `Take ${take2!.take_version}를 대본 사용 기준으로 다시 평가해 참고 결과를 만들어요`,
        before: { script_used: false },
        after: { script_used: true },
        notes: ['참고 결과이고 공식 결과는 그대로예요'],
        approved: [
          {
            t: 'claim',
            data: say('ACTION_RESULT', '참고 결과를 만들었어요. 공식 리뷰는 그대로예요.'),
          },
          {
            t: 'end',
            status: 'DONE',
            cards: () => [
              {
                type: 'REASSESS_RESULT',
                // 홈 목에서 점수가 있는 Take(2, 72점) — Take 3 은 점수가 없습니다
                take_id: take2!.take_id,
                change: { script_used: true },
                before: {
                  score: take2!.score,
                  dimensions: { audience_gaze: 'NEEDS_WORK', delivery: 'STABLE' },
                  criteria_results: [],
                },
                after: {
                  score: 78,
                  dimensions: { audience_gaze: null, delivery: 'STABLE' },
                  criteria_results: [],
                },
                changed_fields: ['score', 'dimensions.audience_gaze'],
                official_differs: false,
              },
            ],
          },
        ],
        rejected: '다시 평가하지 않았어요.',
      },
    ];
  if (text.includes('발표할래') || text.includes('연습 준비'))
    return [
      // SYSTEM 은 코드 목록의 문장만(6-9) — 안내는 사용법 자료 문장으로 냅니다
      ...guideOnly('연습 시작', "자료 '연습 시작하기'"),
      {
        t: 'claim',
        data: say(
          'GUIDE',
          '연습 준비 화면에서 대본 보기 방식을 고르고 시작하면 돼요.',
          { source_ids: [practiceGuide.source_id] },
          { sources: [practiceGuide] },
        ),
      },
      { t: 'end', status: 'DONE', cards: () => [{ type: 'START_PRACTICE', pitch_id: PITCH_ID }] },
    ];
  if (text.includes('기억'))
    return [
      // 자료 없음: 고정 안내 뒤 일반 조언으로 DONE (6-10 · 7-10)
      ...guideOnly('짧게 설명', '찾은 자료 없음', 'EMPTY'),
      { t: 'claim', data: say('SYSTEM', '자료에서 확인한 내용은 아니고 일반적인 조언이에요.') },
      { t: 'claim', data: say('GENERAL', '핵심을 먼저 말하고 이유는 짧게 덧붙여 볼게요.') },
      {
        t: 'end',
        status: 'DONE',
        cards: () =>
          ctx.memoryOn
            ? [{ type: 'MEMORY_SAVED', item_id: '', key: 'TONE', value: '짧게 설명' }]
            : [],
      },
    ];
  if (text.includes('봤지')) return assessmentSteps(text, ctx);
  if (ctx.screen?.name === 'pitch_create' || ctx.screen?.name === 'pitch_edit')
    return screenSteps(ctx.screen);
  const [guideClaim, general] = more;
  return [
    ...base(),
    { t: 'claim', data: first! },
    { t: 'claim', data: guideClaim! },
    // 일반 조언 묶음 바로 앞에는 언제나 고정 안내가 붙습니다 — 자료가 있어도 (7-10, AI 스택 #921 뒤 개정)
    { t: 'claim', data: say('SYSTEM', '자료에서 확인한 내용은 아니고 일반적인 조언이에요.') },
    { t: 'claim', data: general! },
    { t: 'end', status: 'DONE', cards: () => [] },
  ];
}

function proposedGoal(goal: Goal): ProposedGoal {
  const proposed: Partial<Goal> = { ...goal };
  delete proposed.version;
  delete proposed.status;
  delete proposed.created_at;
  return proposed as ProposedGoal;
}

/** 다시 읽은 값으로 만든 목표 저장됨 카드 (5-2). 고른 안이 있으면 그 안의 목표 · 이유 · 근거입니다 */
function goalSavedCard(actionId: string, chosen: GoalOption | null = null): Card {
  const goal = savedGoal(1);
  return {
    type: 'GOAL_SAVED',
    action_id: actionId,
    goal: chosen?.goal ? { ...goal, ...chosen.goal } : goal,
    reason: chosen?.reason ?? '3장 청중 보기 비율이 가장 낮아요.',
    evidence_ids: chosen?.evidence_ids ?? ['ev-gaze-diff'],
    undo: true,
  };
}
