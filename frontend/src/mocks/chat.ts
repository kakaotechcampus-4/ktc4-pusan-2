import { http, HttpResponse, type PathParams } from 'msw';
import { chatPaths } from '@/shared/api/chatPaths';
import { currentMockUserId } from './auth';
import { homeFixture } from './home';
import type {
  ActionDecisionResponse,
  Assessment,
  Card,
  ChatErrorCode,
  ChatMessage,
  ClaimEventData,
  ConversationSummary,
  Goal,
  ProposedGoal,
  Evidence,
  GuideCard,
  PlanStep,
  ProfileItem,
  RunEvent,
  RunStatus,
  RunView,
  SendMessageAccepted,
  SendMessageRequest,
  TraceItem,
} from '@/types/chat';
import { TERMINAL_RUN_STATUSES } from '@/types/chat';

/**
 * 대화형 코치 에이전트 목 (`docs/ai/features/chat-agent.md` 12-3 · 12-4, 개정판 #900).
 *
 * BE 화면용 API 가 없어서 **값 · 동작은 문서 그대로, 경로는 `chatPaths` 의 잠정 경로**입니다.
 * 실행은 시계가 아니라 **조회할 때마다 한 단계씩** 나아갑니다 — 화면이 1초마다 조회하면 1초에 한 문장이
 * 오고, 시험은 타이머 없이 순서를 확인할 수 있습니다.
 *
 * 보낸 말로 시나리오를 고릅니다. **위에서부터 처음 맞는 하나**입니다 (예: "목표 제안해 줘" 는 제안):
 *   (제안 카드에서 고름) → 승인 없이 바로 목표 저장됨
 *   "날씨"            → REFUSED (발표와 관계없는 질문)
 *   "제안"            → 목표 제안 카드(사용자 안 · 추천 안)
 *   "목표"            → 할 일 · 도구 · 판단 문장 → 승인 카드(SAVE_GOAL) 대기 → [승인] 목표 저장됨 / [거절]
 *   "다시 평가" · "대본 보고" → 승인 카드(REASSESS) → [승인] 참고 결과 카드
 *   "발표할래" · "연습 준비" → [연습 준비로] 버튼 카드
 *   "기억"            → '기억했어요' 카드 (기억이 켜져 있고 '기억 없이' 대화가 아닐 때)
 *   그 밖              → Take 비교 답: 내 기록 · 자료 · 일반 조언 문장 셋
 *
 * 콘솔(개발 중에만): `mockChat.fail('AGENT_BUSY')` 다음 보내기 실패 · `mockChat.runFail('AGENT_TIMEOUT')`
 * 다음 실행이 첫 문장 뒤 실패 · `mockChat.partial()` 다음 실행이 첫 문장 뒤 PARTIAL (행동 성공 문장 뒤에서는
 * 끊지 않습니다 — 완료 문장은 성공 기록으로만 나옵니다) · `mockChat.expire()`
 * 승인 대기를 만료 · `mockChat.disable()` / `enable()` / `unreachable()` 사용 가능 여부 · `mockChat.ok()` 되돌리기.
 */

const pitch = homeFixture.pitches[0]!;
const PITCH_ID = pitch.pitch_id;
const [take1, take2, take3] = pitch.takes;

const error = (code: string, status: number, headers?: Record<string, string>) =>
  HttpResponse.json({ code, message: code, detail: null }, { status, headers });

/** UUID 모양의 목 ID — 화면이 ID 형식을 검사해도 통과합니다 */
function idMaker(prefix: string) {
  let n = 0;
  return () => `${prefix}-0000-4000-8000-${String(++n).padStart(12, '0')}`;
}

/** Python `len` 과 같은 글자 수(코드 포인트) */
const codePoints = (s: string) => [...s].length;

/** 다음 `Asia/Seoul` 00:00 까지의 초 */
function secondsToSeoulMidnight(now = Date.now()) {
  const kst = now + 9 * 3600_000;
  const next = Math.floor(kst / 86_400_000 + 1) * 86_400_000;
  return Math.ceil((next - kst) / 1000);
}

// ── 고정 자료 ─────────────────────────────────────────────────

const evidence = (
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

const BASIS_LABEL = `Take ${take3!.take_version} · 대본 V2 · 기준 v4 기준`;

type ClaimStep = Omit<ClaimEventData, 'index'>;

const say = (
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

function compareClaims(): ClaimStep[] {
  const a = gazeOf(take2, 0.31, 'ev-gaze-t2');
  const b = gazeOf(take3, 0.24, 'ev-gaze-t3');
  const diff = evidence({
    evidence_id: 'ev-gaze-diff',
    origin: 'COMPUTED',
    kind: null,
    revision: null,
    path: 'computed.diff(audience_ratio, slide 3)',
    metric: 'audience_ratio',
    slide_number: 3,
    value: -0.07,
    inputs: [a.evidence_id, b.evidence_id],
    calc: 'DIFF',
  });
  return [
    say(
      'RECORD',
      `3장에서 청중을 본 비율이 Take ${take2!.take_version} 31%에서 Take ${take3!.take_version} 24%로 7%p 줄었어요.`,
      { evidence_ids: [diff.evidence_id] },
      { evidence: [a, b, diff] },
    ),
    say(
      'GUIDE',
      '장마다 첫 낱말만 보고 말을 이어 가 보세요.',
      { source_ids: [guide.source_id] },
      { sources: [guide] },
    ),
    say('GENERAL', '말하기 전에 한 번 숨을 고르고 청중 쪽을 먼저 보면 시작이 안정돼요.'),
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

type Step =
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
];

function scenario(text: string, ctx: { memoryOn: boolean; choice: boolean }): Step[] {
  const [first, ...more] = compareClaims();
  if (ctx.choice)
    return [
      { t: 'claim', data: say('ACTION_RESULT', '목표를 저장했어요.') },
      { t: 'end', status: 'DONE', cards: (id) => [goalSavedCard(id)] },
    ];
  if (text.includes('날씨'))
    return [
      { t: 'claim', data: say('SYSTEM', '발표 연습과 관계없는 질문이에요.') },
      { t: 'end', status: 'REFUSED', cards: () => [] },
    ];
  if (text.includes('제안'))
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
                {
                  label: 'USER',
                  goal: { ...proposed, slide_number: null },
                  reason: '전체 장을 한 번에 올리기는 기록상 어려워요.',
                  evidence_ids: [],
                  content_hash: 'hash-user',
                },
                {
                  label: 'EXPERT',
                  goal: proposed,
                  reason: '가장 낮은 3장부터 올려요.',
                  evidence_ids: ['ev-gaze-diff'],
                  content_hash: 'hash-expert',
                },
              ],
            },
          ];
        },
      },
    ];
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
        summary: `Take ${take3!.take_version}을 대본 사용 기준으로 다시 평가해 참고 결과를 만들어요`,
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
                take_id: take3!.take_id,
                change: 'SCRIPT_USED',
                before: {
                  score: 68,
                  dimensions: { audience_gaze: 'NEEDS_WORK', delivery: 'STABLE' },
                  criteria_results: {},
                },
                after: {
                  score: 74,
                  dimensions: { audience_gaze: null, delivery: 'STABLE' },
                  criteria_results: {},
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
      { t: 'claim', data: say('SYSTEM', '준비되면 연습 준비 화면에서 모드를 고르고 시작하세요.') },
      { t: 'end', status: 'DONE', cards: () => [{ type: 'START_PRACTICE', pitch_id: PITCH_ID }] },
    ];
  if (text.includes('기억'))
    return [
      { t: 'claim', data: say('GENERAL', '앞으로는 짧게 설명할게요.') },
      {
        t: 'end',
        status: 'DONE',
        cards: () =>
          ctx.memoryOn
            ? [{ type: 'MEMORY_SAVED', item_id: '', key: 'TONE', value: '짧게 설명' }]
            : [],
      },
    ];
  return [
    ...base(),
    ...[first!, ...more].map((data): Step => ({ t: 'claim', data })),
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

function goalSavedCard(actionId: string): Card {
  return {
    type: 'GOAL_SAVED',
    action_id: actionId,
    goal: savedGoal(1),
    reason: '3장 청중 보기 비율이 가장 낮아요.',
    evidence_ids: ['ev-gaze-diff'],
    undo: true,
  };
}

// ── 사용자별 상태 ─────────────────────────────────────────────

interface Run {
  run_id: string;
  conversation_id: string;
  status: RunStatus;
  steps: Step[];
  events: RunEvent[];
  plan: PlanStep[];
  trace: TraceItem[];
  claims: ClaimStep[];
  cards: Card[];
  error: { code: ChatErrorCode } | null;
  retry_after_s: number | null;
  /** 승인 대기 중인 행동 */
  ask:
    | (Extract<Step, { t: 'ask' }> & { action_id: string; args_hash: string; expires_at: string })
    | null;
  /** 다음 실행에 걸어 둔 실패 — 첫 문장 뒤 끝냅니다 */
  failAfterFirst: { status: 'PARTIAL' | 'FAILED'; code: ChatErrorCode | null } | null;
  /** 승인받은 행동. 그 결과 카드(목표 저장됨)는 같은 `action_id` 를 씁니다 — 승인한 그 행동의 결과입니다 */
  approvedActionId: string | null;
}

/** msw 가 넘기는 것 중 이 목이 쓰는 칸 */
interface Info {
  request: Request;
  params: PathParams;
}

interface Conversation extends ConversationSummary {
  messages: ChatMessage[];
}

interface UserState {
  conversations: Map<string, Conversation>;
  runs: Map<string, Run>;
  requests: Map<string, { body: string; accepted: SendMessageAccepted }>;
  decisions: Map<string, ActionDecisionResponse>;
  undone: Map<string, { action_id: string; goal_version: number }>;
  goalVersion: number;
  /** 저장된 목표의 `action_id` 와 그 판 */
  goalAction: { action_id: string; version: number } | null;
  memory: { enabled: boolean; items: ProfileItem[] };
}

type Failure = { code: ChatErrorCode; status: number } | null;

export interface MockChatControls {
  fail: (code?: ChatErrorCode) => void;
  runFail: (code?: ChatErrorCode) => void;
  partial: () => void;
  expire: () => void;
  disable: () => void;
  enable: () => void;
  unreachable: () => void;
  ok: () => void;
}

const SEND_FAILURE_STATUS: Partial<Record<ChatErrorCode, number>> = {
  AGENT_BUSY: 429,
  AGENT_DAILY_LIMIT: 429,
  AGENT_CONTEXT_TOO_LARGE: 413,
  VALIDATION_ERROR: 422,
  AGENT_DISABLED: 503,
};

export function createChatHandlers(userId: () => string | null = currentMockUserId) {
  const users = new Map<string, UserState>();
  const newConversationId = idMaker('40000000');
  const newMessageId = idMaker('41000000');
  const newRunId = idMaker('42000000');
  const newActionId = idMaker('43000000');
  const newItemId = idMaker('44000000');

  let availability: 'on' | 'off' | 'unreachable' = 'on';
  let sendFailure: Failure = null;
  let runFailure: Run['failAfterFirst'] = null;

  const controls: MockChatControls = {
    fail: (code = 'AGENT_BUSY') => {
      sendFailure = { code, status: SEND_FAILURE_STATUS[code] ?? 409 };
    },
    runFail: (code = 'AGENT_TIMEOUT') => {
      runFailure = { status: 'FAILED', code };
    },
    partial: () => {
      runFailure = { status: 'PARTIAL', code: null };
    },
    expire: () => {
      for (const state of users.values())
        for (const run of state.runs.values())
          if (run.status === 'WAITING_APPROVAL')
            settleAsk(
              state,
              run,
              'EXPIRED',
              '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.',
            );
    },
    disable: () => {
      availability = 'off';
    },
    enable: () => {
      availability = 'on';
    },
    unreachable: () => {
      availability = 'unreachable';
    },
    ok: () => {
      availability = 'on';
      sendFailure = null;
      runFailure = null;
    },
  };

  function stateOf(id: string): UserState {
    let state = users.get(id);
    if (!state) {
      state = {
        conversations: new Map(),
        runs: new Map(),
        requests: new Map(),
        decisions: new Map(),
        undone: new Map(),
        goalVersion: 0,
        goalAction: null,
        memory: { enabled: true, items: [] },
      };
      seed(state);
      users.set(id, state);
    }
    return state;
  }

  /** 지난 대화 하나 — 최신 아님 배지(`NEW_TAKE`)를 볼 수 있게 둡니다 */
  function seed(state: UserState) {
    const id = newConversationId();
    const [claim] = compareClaims();
    state.conversations.set(id, {
      conversation_id: id,
      title: '지난번보다 청중을 더 봤어?',
      scope_pitch_id: PITCH_ID,
      scope_title: pitch.pitch_title,
      last_message_at: '2026-10-28T09:00:00Z',
      has_stale: true,
      memory_off: false,
      messages: [
        userMessage('지난번보다 청중을 더 봤어?', '2026-10-28T08:59:50Z'),
        {
          ...assistantMessage(newRunId(), 'DONE', [claim!], [], [], []),
          created_at: '2026-10-28T09:00:00Z',
          basis_label: `Take ${take1!.take_version} · 대본 V1 · 기준 v3 기준`,
          stale_reasons: ['NEW_TAKE'],
        },
      ],
    });
  }

  function userMessage(text: string, at = new Date().toISOString()): ChatMessage {
    return {
      message_id: newMessageId(),
      role: 'user',
      text,
      created_at: at,
      run_id: null,
      status: null,
      claims: [],
      cards: [],
      assessments: [],
      evidence: [],
      sources: [],
      basis_label: null,
      stale_reasons: [],
      plan: [],
      trace: [],
    };
  }

  function assistantMessage(
    runId: string,
    status: RunStatus,
    claims: ClaimStep[],
    cards: Card[],
    planSteps: PlanStep[],
    traceItems: TraceItem[],
  ): ChatMessage {
    const assessments: Assessment[] = claims.flatMap((c) => c.assessments);
    return {
      message_id: newMessageId(),
      role: 'assistant',
      text: claims.map((c) => c.claim.text).join('\n'),
      created_at: new Date().toISOString(),
      run_id: runId,
      status,
      claims: claims.map((c) => c.claim),
      cards,
      assessments: assessments.map((a) => ({ ...a, superseded_by: null })),
      evidence: claims.flatMap((c) => c.evidence),
      sources: claims.flatMap((c) => c.sources),
      basis_label: BASIS_LABEL,
      stale_reasons: [],
      plan: planSteps,
      trace: traceItems,
    };
  }

  /** 이벤트 종류와 데이터 모양이 짝이 맞는지 부르는 쪽에서 검사합니다 */
  function pushEvent<E extends RunEvent['event']>(
    run: Run,
    event: E,
    data: Extract<RunEvent, { event: E }>['data'],
  ) {
    run.events.push({ event_seq: run.events.length + 1, event, data } as RunEvent);
  }

  function emitClaim(run: Run, data: ClaimStep) {
    run.claims.push(data);
    pushEvent(run, 'claim', { index: run.claims.length - 1, ...data });
  }

  function finish(state: UserState, run: Run, status: RunStatus, cards: Card[]) {
    run.status = status;
    run.cards = cards;
    run.ask = null;
    const conversation = state.conversations.get(run.conversation_id)!;
    if (run.claims.length > 0 || cards.length > 0) {
      conversation.messages.push(
        assistantMessage(run.run_id, status, run.claims, cards, run.plan, run.trace),
      );
      conversation.last_message_at = new Date().toISOString();
    }
  }

  /** 승인 대기를 거절 · 만료 · 취소로 끝냅니다 — AI 를 다시 부르지 않고 BE 고정 문장을 덧붙입니다 */
  function settleAsk(state: UserState, run: Run, status: RunStatus, sentence: string | null) {
    const ask = run.ask!;
    const card: Card = askCard(ask);
    if (sentence) emitClaim(run, say('SYSTEM', sentence));
    finish(state, run, status, [card]);
    state.decisions.set(ask.action_id, {
      action_id: ask.action_id,
      action_status: status === 'DONE' ? 'REJECTED' : (status as 'EXPIRED' | 'CANCELLED'),
      run_id: run.run_id,
      run_status: status,
    });
  }

  function askCard(ask: NonNullable<Run['ask']>): Card {
    return {
      type: 'ASK_CONFIRM',
      action_id: ask.action_id,
      action_type: ask.action_type,
      title: ask.title,
      summary: ask.summary,
      before: ask.before,
      after: ask.after,
      notes: ask.notes,
      expires_at: ask.expires_at,
      args_hash: ask.args_hash,
    };
  }

  /** 조회 한 번에 한 단계 */
  function advance(state: UserState, run: Run) {
    if (TERMINAL_RUN_STATUSES.includes(run.status) || run.status === 'WAITING_APPROVAL') return;
    run.status = 'RUNNING';
    const step = run.steps.shift();
    if (!step) return finish(state, run, 'DONE', []);
    switch (step.t) {
      case 'plan':
        run.plan = step.plan;
        return pushEvent(run, 'plan', { plan: step.plan });
      case 'tool':
        run.trace.push(step.trace);
        return pushEvent(run, 'tool_done', { trace: step.trace });
      case 'claim':
        emitClaim(run, step.data);
        if (run.failAfterFirst && step.data.claim.kind !== 'ACTION_RESULT') {
          const { status, code } = run.failAfterFirst;
          run.error = code ? { code } : null;
          finish(state, run, status, []);
        }
        return;
      case 'ask': {
        const actionId = newActionId();
        run.ask = {
          ...step,
          action_id: actionId,
          args_hash: `hash-${actionId.slice(-4)}`,
          expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
        };
        run.status = 'WAITING_APPROVAL';
        run.cards = [askCard(run.ask)];
        return;
      }
      case 'end': {
        // 승인 없이 바로 실행한 행동(제안 고르기)만 새 ID 를 받습니다
        const actionId = run.approvedActionId ?? newActionId();
        let cards = step.cards(actionId);
        cards = cards.map((card) => {
          if (card.type === 'GOAL_SAVED') {
            state.goalVersion += 1;
            state.goalAction = { action_id: actionId, version: state.goalVersion };
            return { ...card, goal: savedGoal(state.goalVersion) };
          }
          if (card.type === 'MEMORY_SAVED') {
            const item: ProfileItem = { item_id: newItemId(), key: card.key, value: card.value };
            state.memory.items = [...state.memory.items.filter((i) => i.key !== item.key), item];
            return { ...card, item_id: item.item_id };
          }
          return card;
        });
        return finish(state, run, step.status, cards);
      }
    }
  }

  function runView(run: Run, afterSeq: number): RunView {
    return {
      run_id: run.run_id,
      conversation_id: run.conversation_id,
      status: run.status,
      plan: run.plan,
      trace: run.trace,
      events: run.events.filter((e) => e.event_seq > afterSeq),
      next_after_seq: null,
      cards: run.status === 'WAITING_APPROVAL' ? run.cards : [],
      error: run.error,
      retry_after_s: run.retry_after_s,
    };
  }

  const activeRun = (state: UserState, conversationId: string) =>
    [...state.runs.values()].find(
      (r) => r.conversation_id === conversationId && !TERMINAL_RUN_STATUSES.includes(r.status),
    ) ?? null;

  const summary = (c: Conversation): ConversationSummary => ({
    conversation_id: c.conversation_id,
    title: c.title,
    scope_pitch_id: c.scope_pitch_id,
    scope_title: c.scope_title,
    last_message_at: c.last_message_at,
    has_stale: c.has_stale,
    memory_off: c.memory_off,
  });

  function withUser(fn: (state: UserState, info: Info) => Response | Promise<Response>) {
    return (info: Info) => {
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      return fn(stateOf(id), info);
    };
  }

  const handlers = [
    http.get(`*${chatPaths.availability}`, () => {
      if (availability === 'unreachable') return HttpResponse.error();
      return HttpResponse.json({ enabled: availability === 'on' });
    }),

    http.get(
      `*${chatPaths.conversations}`,
      withUser((state, { request }) => {
        const pitchId = new URL(request.url).searchParams.get('pitch_id');
        const items = [...state.conversations.values()]
          .filter((c) => !pitchId || c.scope_pitch_id === pitchId)
          .map(summary)
          .sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''));
        return HttpResponse.json({ items });
      }),
    ),

    http.post(
      `*${chatPaths.conversations}`,
      withUser(async (state, { request }) => {
        const body = (await request.json().catch(() => null)) as {
          scope_pitch_id?: unknown;
          memory_off?: unknown;
        } | null;
        if (!body || typeof body.memory_off !== 'boolean') return error('VALIDATION_ERROR', 422);
        const conversation = createConversation(
          state,
          body.scope_pitch_id as string | null,
          '',
          body.memory_off,
        );
        return HttpResponse.json(
          { conversation_id: conversation.conversation_id },
          { status: 201 },
        );
      }),
    ),

    http.get(
      `*${chatPaths.messages(':conversationId')}`,
      withUser((state, { params }) => {
        const conversation = state.conversations.get(String(params.conversationId));
        if (!conversation) return error('NOT_FOUND', 404);
        return HttpResponse.json({ items: conversation.messages, next_cursor: null });
      }),
    ),

    http.put(
      `*${chatPaths.scope(':conversationId')}`,
      withUser(async (state, { params, request }) => {
        const conversation = state.conversations.get(String(params.conversationId));
        if (!conversation) return error('NOT_FOUND', 404);
        if (activeRun(state, conversation.conversation_id))
          return error('AGENT_RUN_IN_PROGRESS', 409);
        const body = (await request.json().catch(() => null)) as { pitch_id?: unknown } | null;
        const pitchId = body?.pitch_id ?? null;
        if (pitchId !== null && pitchId !== PITCH_ID) return error('NOT_FOUND', 404);
        conversation.scope_pitch_id = pitchId;
        conversation.scope_title = pitchId ? pitch.pitch_title : null;
        return HttpResponse.json({
          conversation_id: conversation.conversation_id,
          scope_pitch_id: conversation.scope_pitch_id,
          scope_title: conversation.scope_title,
        });
      }),
    ),

    http.post(
      `*${chatPaths.send}`,
      withUser(async (state, { request }) => {
        const raw = await request.text();
        const body = parseSend(raw);
        if (!body) return error('VALIDATION_ERROR', 422);
        const seen = state.requests.get(body.client_request_id);
        if (seen)
          return seen.body === canonical(body)
            ? HttpResponse.json(seen.accepted, { status: 202 })
            : error('AGENT_REQUEST_CONFLICT', 409);
        if (sendFailure) {
          const { code, status } = sendFailure;
          sendFailure = null;
          if (code === 'AGENT_BUSY') return error(code, status, { 'Retry-After': '5' });
          if (code === 'AGENT_DAILY_LIMIT')
            return error(code, status, { 'Retry-After': String(secondsToSeoulMidnight()) });
          return error(code, status);
        }
        let conversation: Conversation | undefined;
        if (body.conversation_id) {
          conversation = state.conversations.get(body.conversation_id);
          if (!conversation) return error('NOT_FOUND', 404);
        } else {
          conversation = createConversation(state, body.scope_pitch_id, body.text, false);
        }
        const running = activeRun(state, conversation.conversation_id);
        if (running?.status === 'WAITING_APPROVAL')
          settleAsk(state, running, 'CANCELLED', '새 메시지를 받아 앞의 확인 요청은 취소했어요.');
        else if (running) return error('AGENT_RUN_IN_PROGRESS', 409);

        if (!conversation.title) conversation.title = [...body.text].slice(0, 30).join('');
        const message = userMessage(body.text);
        conversation.messages.push(message);
        conversation.last_message_at = message.created_at;
        const run: Run = {
          run_id: newRunId(),
          conversation_id: conversation.conversation_id,
          status: 'QUEUED',
          steps: scenario(body.text, {
            memoryOn: state.memory.enabled && !conversation.memory_off,
            choice: body.choice !== null,
          }),
          events: [],
          plan: [],
          trace: [],
          claims: [],
          cards: [],
          error: null,
          retry_after_s: null,
          ask: null,
          failAfterFirst: runFailure,
          approvedActionId: null,
        };
        runFailure = null;
        state.runs.set(run.run_id, run);
        const accepted: SendMessageAccepted = {
          conversation_id: conversation.conversation_id,
          message_id: message.message_id,
          run_id: run.run_id,
          status: 'QUEUED',
        };
        state.requests.set(body.client_request_id, { body: canonical(body), accepted });
        return HttpResponse.json(accepted, { status: 202 });
      }),
    ),

    http.get(
      `*${chatPaths.run(':runId')}`,
      withUser((state, { params, request }) => {
        const run = state.runs.get(String(params.runId));
        if (!run) return error('NOT_FOUND', 404);
        const afterSeq = Number(new URL(request.url).searchParams.get('after_seq') ?? 0);
        if (!Number.isInteger(afterSeq) || afterSeq < 0) return error('VALIDATION_ERROR', 422);
        advance(state, run);
        return HttpResponse.json(runView(run, afterSeq));
      }),
    ),

    http.post(
      `*${chatPaths.cancel(':runId')}`,
      withUser((state, { params }) => {
        const run = state.runs.get(String(params.runId));
        if (!run) return error('NOT_FOUND', 404);
        // [취소]는 새 메시지가 아니라서 "새 메시지를 받아 …" 문장을 붙이지 않습니다
        if (run.status === 'WAITING_APPROVAL') settleAsk(state, run, 'CANCELLED', null);
        else if (!TERMINAL_RUN_STATUSES.includes(run.status)) finish(state, run, 'CANCELLED', []);
        // 먼저 끝난 쪽이 이깁니다 — 이미 끝났으면 그 상태를 그대로 돌려줍니다
        return HttpResponse.json(runView(run, run.events.length));
      }),
    ),

    http.post(
      `*${chatPaths.decision(':actionId')}`,
      withUser(async (state, { params, request }) => {
        const actionId = String(params.actionId);
        const done = state.decisions.get(actionId);
        const body = (await request.json().catch(() => null)) as {
          decision?: unknown;
          args_hash?: unknown;
        } | null;
        if (
          !body ||
          (body.decision !== 'APPROVE' && body.decision !== 'REJECT') ||
          typeof body.args_hash !== 'string'
        )
          return error('VALIDATION_ERROR', 422);
        if (done) {
          if (done.action_status === 'EXPIRED') return error('AGENT_EXPIRED', 409);
          if (done.action_status === 'CANCELLED') return error('AGENT_CANCELLED', 409);
          return HttpResponse.json(done);
        }
        const run = [...state.runs.values()].find((r) => r.ask?.action_id === actionId);
        if (!run?.ask) return error('NOT_FOUND', 404);
        if (body.args_hash !== run.ask.args_hash) return error('AGENT_STATE_INVALID', 409);
        if (body.decision === 'REJECT') {
          settleAsk(state, run, 'DONE', run.ask.rejected);
          return HttpResponse.json(state.decisions.get(actionId));
        }
        run.steps = [...run.ask.approved];
        run.approvedActionId = actionId;
        run.ask = null;
        run.cards = [];
        run.status = 'RUNNING';
        const response: ActionDecisionResponse = {
          action_id: actionId,
          action_status: 'RUNNING',
          run_id: run.run_id,
          run_status: 'RUNNING',
        };
        state.decisions.set(actionId, response);
        return HttpResponse.json(response);
      }),
    ),

    http.post(
      `*${chatPaths.undo(':actionId')}`,
      withUser((state, { params }) => {
        const actionId = String(params.actionId);
        const again = state.undone.get(actionId);
        if (again) return HttpResponse.json(again);
        if (!state.goalAction || state.goalAction.action_id !== actionId)
          return error('AGENT_STATE_INVALID', 409);
        state.goalVersion += 1;
        state.goalAction = null;
        const result = { action_id: actionId, goal_version: state.goalVersion };
        state.undone.set(actionId, result);
        const conversation = [...state.conversations.values()].find((c) =>
          c.messages.some((m) =>
            m.cards.some((card) => card.type === 'GOAL_SAVED' && card.action_id === actionId),
          ),
        );
        conversation?.messages.push({ ...userMessage('목표를 되돌렸어요.'), role: 'system' });
        return HttpResponse.json(result);
      }),
    ),

    http.get(
      `*${chatPaths.memory}`,
      withUser((state) => HttpResponse.json(state.memory)),
    ),

    http.put(
      `*${chatPaths.memory}`,
      withUser(async (state, { request }) => {
        const body = (await request.json().catch(() => null)) as { enabled?: unknown } | null;
        if (typeof body?.enabled !== 'boolean') return error('VALIDATION_ERROR', 422);
        state.memory = { ...state.memory, enabled: body.enabled };
        return HttpResponse.json(state.memory);
      }),
    ),

    http.delete(
      `*${chatPaths.memoryItems}`,
      withUser((state) => {
        state.memory = { ...state.memory, items: [] };
        return HttpResponse.json(state.memory);
      }),
    ),

    http.delete(
      `*${chatPaths.memoryItem(':itemId')}`,
      withUser((state, { params }) => {
        const itemId = String(params.itemId);
        if (!state.memory.items.some((i) => i.item_id === itemId)) return error('NOT_FOUND', 404);
        state.memory = {
          ...state.memory,
          items: state.memory.items.filter((i) => i.item_id !== itemId),
        };
        return HttpResponse.json(state.memory);
      }),
    ),
  ];

  function createConversation(
    state: UserState,
    scope: string | null,
    title: string,
    memoryOff: boolean,
  ) {
    const conversation: Conversation = {
      conversation_id: newConversationId(),
      title: [...title].slice(0, 30).join(''),
      scope_pitch_id: scope,
      scope_title: scope === PITCH_ID ? pitch.pitch_title : null,
      last_message_at: null,
      has_stale: false,
      memory_off: memoryOff,
      messages: [],
    };
    state.conversations.set(conversation.conversation_id, conversation);
    return conversation;
  }

  return { handlers, controls };
}

/** 키 순서와 상관없이 같은 값이면 같은 글 */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object')
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableJson((value as Record<string, unknown>)[k])}`)
      .join(',')}}`;
  return JSON.stringify(value);
}

/** 같은 요청인지 볼 때 쓰는 본문 — 멱등 키는 빼고, 키 순서와 상관없이 같게 */
function canonical(body: SendMessageRequest) {
  return stableJson({ ...body, client_request_id: null });
}

/** 메시지 보내기 본문 검사 (12-3) — 공백뿐 · 2,000자 초과 · 모르는 값은 저장 전 422 */
function parseSend(raw: string): SendMessageRequest | null {
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
  const text = typeof body.text === 'string' ? body.text.trim() : '';
  if (!text || codePoints(text) > 2000) return null;
  if (typeof body.client_request_id !== 'string' || !body.client_request_id) return null;
  if (!body.page || typeof body.page !== 'object') return null;
  const choice = body.choice ?? null;
  if (
    choice !== null &&
    !(
      typeof choice === 'object' &&
      typeof (choice as { proposal_id?: unknown }).proposal_id === 'string' &&
      ['USER', 'EXPERT'].includes(String((choice as { option?: unknown }).option))
    )
  )
    return null;
  return {
    conversation_id: typeof body.conversation_id === 'string' ? body.conversation_id : null,
    scope_pitch_id: typeof body.scope_pitch_id === 'string' ? body.scope_pitch_id : null,
    text,
    page: body.page as SendMessageRequest['page'],
    client_request_id: body.client_request_id,
    again_of_message_id:
      typeof body.again_of_message_id === 'string' ? body.again_of_message_id : null,
    choice: choice as SendMessageRequest['choice'],
  };
}

const chat = createChatHandlers();
export const chatHandlers = chat.handlers;

declare global {
  interface Window {
    /** 개발 중 대화 에이전트 목 조작. 프로덕션 번들에는 들어가지 않습니다 */
    mockChat?: MockChatControls;
  }
}

// 목 시험은 node 환경이라 window 가 없습니다
if (import.meta.env.DEV && typeof window !== 'undefined') window.mockChat = chat.controls;
