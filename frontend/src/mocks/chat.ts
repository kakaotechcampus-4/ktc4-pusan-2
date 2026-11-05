import { http, HttpResponse, type PathParams } from 'msw';
import { chatPaths } from '@/shared/api/chatPaths';
import { currentMockUserId } from './auth';
import {
  pitch,
  PITCH_ID,
  take1,
  take2,
  take3,
  say,
  percent,
  compareClaims,
  evidence,
  scenario,
  type ClaimStep,
  type GoalOption,
  type Step,
} from './chatScenarios';
import type {
  ActionDecisionResponse,
  Assessment,
  Card,
  ChatErrorCode,
  ChatMessage,
  ConversationSummary,
  MessageBasis,
  UndoResponse,
  PlanStep,
  ProfileItem,
  RunEvent,
  RunStatus,
  RunView,
  SendMessageAccepted,
  SendMessageRequest,
  TraceItem,
} from '@/types/chat';
import { isTerminalRun } from '@/shared/lib/chatAgent';

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
 *   "제안"            → 목표 제안 카드(사용자 안 · 추천 안). "자신감" 이 있으면 사용자 안은 잴 수 없는 안(goal null)
 *   "목표"            → 할 일 · 도구 · 판단 문장 → 승인 카드(SAVE_GOAL) 대기 → [승인] 목표 저장됨 / [거절]
 *   "다시 평가" · "대본 보고" → 승인 카드(REASSESS) → [승인] 참고 결과 카드
 *   "발표할래" · "연습 준비" → [연습 준비로] 버튼 카드
 *   "기억"            → '기억했어요' 카드 (기억이 켜져 있고 '기억 없이' 대화가 아닐 때)
 *   "봤지"            → 주장 판정: 판단 문장(기록과 다름). 같은 대화에서 다시 물으면 앞 판단을 대체
 *   (피치 만들기 · 수정 화면에서) → 화면 값 근거(`origin:"SCREEN"`) 문장과 사용법 자료
 *   그 밖              → Take 비교 답: 내 기록 · 자료 · 일반 조언 문장 셋
 *
 * 실행은 첫 조회에서 `QUEUED` 를 그대로 보이고, 다음 조회부터 나아갑니다.
 *
 * 콘솔(개발 중에만):
 *   `mockChat.fail('AGENT_BUSY')`      다음 보내기를 그 HTTP 오류로 거절
 *   `mockChat.runFail('AGENT_TIMEOUT')` 다음 실행이 문장 없이 FAILED (12-3 "받은 답이 없으면 FAILED")
 *   `mockChat.partial('AGENT_TIMEOUT')` 다음 실행이 첫 문장 뒤 PARTIAL — 코드 없이 부르면 한도로 멈춘 PARTIAL.
 *                                      행동 성공 문장 뒤에서는 끊지 않습니다(완료 문장은 성공 기록으로만)
 *   `mockChat.expire()`                승인 대기를 만료
 *   `mockChat.goalCheck('ACHIEVED')`   저장된 대화 목표의 자동 확인 결과 (목표마다 한 번)
 *   `mockChat.disable()` · `enable()`   꺼짐 — 대화 API 모두 503 `AGENT_DISABLED`, 사용 가능 여부는 `{enabled:false}`
 *   `mockChat.unreachable()`           사용 가능 여부 조회가 네트워크 오류
 *   `mockChat.ok()`                    모두 되돌리기
 */

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
  /** 걸어 둔 실패 — 문장 없이 FAILED, 또는 첫 문장 뒤 PARTIAL */
  failure: RunFailure | null;
  /**
   * 이 실행의 행동. 승인 카드 · 행동 성공 문장 · 결과 카드(목표 저장됨)가 모두 이 `action_id` 를 씁니다(6-9).
   * 승인이 필요한 행동은 승인 카드를 낼 때, 바로 실행하는 행동(제안 고르기)은 접수할 때 정합니다
   */
  actionId: string | null;
  /** 제안 카드에서 고른 안 */
  chosen: GoalOption | null;
  /** 첫 조회에서 `QUEUED` 를 보였는지 */
  queuedShown: boolean;
}

/** msw 가 넘기는 것 중 이 목이 쓰는 칸 */
interface Info {
  request: Request;
  params: PathParams;
}

interface Conversation extends ConversationSummary {
  /** (BE 미구현) '기억 없이' 대화 — 목 안에서만 들고 있습니다. 만든 뒤에는 바뀌지 않습니다 */
  memory_off: boolean;
  messages: ChatMessage[];
  /** 이 대화의 목표 판 번호 — 처음 0, 저장 · 되돌리기마다 1 올립니다 (5-3 "이 대화의 목표 판 번호") */
  goalVersion: number;
  /** 지금 목표와 그것을 저장한 행동. 되돌리면 null */
  goal: {
    action_id: string;
    version: number;
    slide_number: number | null;
    /** 자동 확인을 마쳤는지 — 같은 리뷰에는 한 번만, 이뤘다 · 못 이뤘다는 끝난 판정입니다 */
    checked: boolean;
  } | null;
}

interface UserState {
  conversations: Map<string, Conversation>;
  runs: Map<string, Run>;
  requests: Map<string, { body: string; accepted: SendMessageAccepted }>;
  decisions: Map<string, ActionDecisionResponse>;
  undone: Map<string, UndoResponse>;
  memory: { enabled: boolean; items: ProfileItem[] };
}

/** 보내기가 HTTP 로 돌려줄 수 있는 실패와 그 상태 (5-9 · 12-3) */
const SEND_FAILURE_STATUS = {
  AGENT_BUSY: 429,
  AGENT_DAILY_LIMIT: 429,
  AGENT_CONTEXT_TOO_LARGE: 413,
  VALIDATION_ERROR: 422,
  AGENT_DISABLED: 503,
  AGENT_NOT_ACCEPTED: 503,
  AGENT_RUN_IN_PROGRESS: 409,
  AGENT_REQUEST_CONFLICT: 409,
  NOT_FOUND: 404,
} as const satisfies Partial<Record<ChatErrorCode, number>>;
type SendFailureCode = keyof typeof SEND_FAILURE_STATUS;

/**
 * 실행이 끝날 때 실행 상태의 `error` 로 실릴 수 있는 코드 — BE 의 AI 오류 허용 목록과 BE 시간 초과 (5-9).
 * 목록 밖 · `LLM_UNAVAILABLE` 은 BE 가 `AGENT_UPSTREAM_ERROR` 로 바꿉니다
 */
type RunErrorCode = Extract<
  ChatErrorCode,
  | 'AGENT_DISABLED'
  | 'AGENT_BUSY'
  | 'AGENT_STATE_INVALID'
  | 'AGENT_CONTEXT_TOO_LARGE'
  | 'AGENT_OUTPUT_TOO_LARGE'
  | 'AGENT_TIMEOUT'
  | 'AGENT_UPSTREAM_ERROR'
>;

type RunFailure =
  { when: 'start'; code: RunErrorCode } | { when: 'afterFirstClaim'; code: RunErrorCode | null };

export interface MockChatControls {
  fail: (code?: SendFailureCode) => void;
  runFail: (code?: RunErrorCode) => void;
  partial: (code?: RunErrorCode) => void;
  expire: () => void;
  goalCheck: (status?: 'ACHIEVED' | 'NOT_ACHIEVED') => void;
  disable: () => void;
  enable: () => void;
  unreachable: () => void;
  ok: () => void;
}

export function createChatHandlers(userId: () => string | null = currentMockUserId) {
  const users = new Map<string, UserState>();
  const newConversationId = idMaker('40000000');
  const newMessageId = idMaker('41000000');
  const newRunId = idMaker('42000000');
  const newActionId = idMaker('43000000');
  const newItemId = idMaker('44000000');
  const newProposalId = idMaker('45000000');
  const newAssessmentId = idMaker('46000000');

  let availability: 'on' | 'off' | 'unreachable' = 'on';
  let sendFailure: SendFailureCode | null = null;
  let runFailure: RunFailure | null = null;

  const controls: MockChatControls = {
    fail: (code = 'AGENT_BUSY') => {
      sendFailure = code;
    },
    runFail: (code = 'AGENT_TIMEOUT') => {
      runFailure = { when: 'start', code };
    },
    partial: (code) => {
      runFailure = { when: 'afterFirstClaim', code: code ?? null };
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
    goalCheck: (status = 'ACHIEVED') => {
      for (const state of users.values())
        for (const conversation of state.conversations.values()) checkGoal(conversation, status);
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
    // 그 뒤에 끝난 Take 3 이 있어 최신 아님(NEW_TAKE)입니다
    const [claim] = compareClaims(take1, take2, [0.22, 0.31], 'ev-old');
    state.conversations.set(id, {
      conversation_id: id,
      title: '지난번보다 청중을 더 봤어?',
      scope_pitch_id: PITCH_ID,
      scope_pitch_title: pitch.pitch_title,
      last_user_activity_at: '2026-10-28T08:59:50Z',
      created_at: '2026-10-28T08:59:50Z',
      memory_off: false,
      goalVersion: 0,
      goal: null,
      messages: [
        userMessage('지난번보다 청중을 더 봤어?', '2026-10-28T08:59:50Z'),
        {
          ...assistantMessage(
            newRunId(),
            'DONE',
            [claim!],
            [],
            [],
            [],
            basisOf(take2!.take_version),
          ),
          created_at: '2026-10-28T09:00:00Z',
          stale_reasons: ['NEW_TAKE'],
        },
      ],
    });
  }

  /**
   * 자동 목표 확인 (5-1 `TAKE_REVIEWED`). 목표 저장 뒤 시작한 Take 의 리뷰가 저장되면 BE 가 부릅니다.
   * 사용자 말 없이 답 메시지만 대화에 남고, 대화창을 열면 보입니다.
   * 목에는 목표 뒤의 Take 가 없어서 홈 목 다음 번호(Take 6)를 씁니다.
   */
  function checkGoal(conversation: Conversation, status: 'ACHIEVED' | 'NOT_ACHIEVED') {
    const goal = conversation.goal;
    if (!goal || goal.checked) return;
    goal.checked = true;
    const nextTake = pitch.takes.length + 1;
    const takeId = `20000000-0000-4000-8000-${String(nextTake).padStart(12, '0')}`;
    const base = 0.24;
    const observed = status === 'ACHIEVED' ? 0.33 : 0.25;
    const where = goal.slide_number === null ? '전체 장' : `${goal.slide_number}장`;
    // 목표 판정은 코드가 계산한 근거(COMPUTED)를 가리키는 기록 문장입니다 (6-8 · 6-9)
    const record = evidence({
      evidence_id: 'ev-goal-observed',
      take_id: takeId,
      take_number: nextTake,
      path: 'take_result.areas.GAZE.metrics.audience_ratio',
      metric: 'audience_ratio',
      slide_number: goal.slide_number,
      value: observed,
    });
    const computed = evidence({
      evidence_id: 'ev-goal-check',
      origin: 'COMPUTED',
      kind: null,
      revision: null,
      path: 'computed.goal_check(audience_ratio)',
      metric: 'audience_ratio',
      slide_number: goal.slide_number,
      value: status,
      inputs: [record.evidence_id],
      calc: 'GOAL_CHECK',
    });
    const verdict = status === 'ACHIEVED' ? '목표를 이뤘어요' : '아직 목표에 닿지 않았어요';
    const card: Card = {
      type: 'GOAL_RESULT',
      goal_version: goal.version,
      take_id: takeId,
      status,
      observed,
      reason: `${where} 청중 보기 비율이 ${percent(observed)}%예요.`,
    };
    conversation.messages.push(
      assistantMessage(
        newRunId(),
        'DONE',
        [
          say(
            'RECORD',
            `${where} 청중 보기 비율이 Take ${take3!.take_version} ${percent(base)}%에서 Take ${nextTake} ${percent(observed)}%가 되어 ${verdict}.`,
            { evidence_ids: [computed.evidence_id] },
            { evidence: [record, computed] },
          ),
        ],
        [card],
        [],
        [],
        basisOf(nextTake),
      ),
    );
  }

  function userMessage(text: string, at = new Date().toISOString()): ChatMessage {
    return {
      message_id: newMessageId(),
      // 순번은 목록을 돌려줄 때 붙입니다 — 메시지는 뒤에만 쌓여 자리가 곧 순번입니다
      seq: 0,
      role: 'user',
      text,
      status: null,
      run_id: null,
      again_of_message_id: null,
      created_at: at,
      // BE 처럼 사용자 · 시스템 메시지의 답 칸은 비어(null) 있습니다
      claims: null,
      cards: null,
      assessments: null,
      evidence: null,
      sources: null,
      basis: null,
      plan: null,
      trace: null,
      masked: false,
      stale_reasons: [],
    };
  }

  function assistantMessage(
    runId: string,
    status: RunStatus,
    claims: ClaimStep[],
    cards: Card[],
    planSteps: PlanStep[],
    traceItems: TraceItem[],
    basis: MessageBasis | null,
  ): ChatMessage {
    const assessments: Assessment[] = claims.flatMap((c) => c.assessments);
    return {
      message_id: newMessageId(),
      seq: 0,
      role: 'assistant',
      text: claims.map((c) => c.claim.text).join('\n'),
      status,
      run_id: runId,
      again_of_message_id: null,
      created_at: new Date().toISOString(),
      claims: claims.map((c) => c.claim),
      cards,
      assessments: assessments.map((a) => ({ ...a, superseded_by: null })),
      evidence: claims.flatMap((c) => c.evidence),
      sources: claims.flatMap((c) => c.sources),
      basis,
      plan: planSteps,
      trace: traceItems,
      masked: false,
      stale_reasons: [],
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
      // 근거 배지는 그 턴이 Take 기록을 읽었을 때만 — 거절 · 사용법만 본 답에는 없습니다 (6-8 basis)
      const readTakes = run.trace.some((t) => t.tool === 'compare_takes');
      conversation.messages.push(
        assistantMessage(
          run.run_id,
          status,
          run.claims,
          cards,
          run.plan,
          run.trace,
          readTakes ? basisOf(take3!.take_version) : null,
        ),
      );
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
    if (isTerminalRun(run.status) || run.status === 'WAITING_APPROVAL') return;
    // 접수 직후 첫 조회는 대기 그대로 — 화면의 대기(2초) → 실행 중(1초) 조회 전환을 볼 수 있게
    if (run.status === 'QUEUED' && !run.queuedShown) {
      run.queuedShown = true;
      return;
    }
    run.status = 'RUNNING';
    // 문장을 하나도 내지 않고 실패 — 받은 답이 없으면 FAILED (12-3)
    if (run.failure?.when === 'start') return failRun(state, run, 'FAILED', run.failure.code);
    const step = run.steps.shift();
    if (!step) return finish(state, run, 'DONE', []);
    switch (step.t) {
      case 'plan':
        run.plan = step.plan;
        return pushEvent(run, 'plan', { plan: step.plan });
      case 'tool':
        run.trace.push(step.trace);
        return pushEvent(run, 'tool_done', { trace: step.trace });
      case 'claim': {
        // 행동 성공 문장의 근거는 그 행동의 action_id 입니다 (6-9 `ACTION_RESULT`)
        const isAction = step.data.claim.kind === 'ACTION_RESULT';
        emitClaim(
          run,
          isAction
            ? { ...step.data, claim: { ...step.data.claim, action_id: run.actionId } }
            : step.data,
        );
        // 받은 문장이 있으면 PARTIAL (12-3). 행동 성공 문장 뒤에서는 끊지 않습니다
        if (run.failure?.when === 'afterFirstClaim' && !isAction)
          failRun(state, run, 'PARTIAL', run.failure.code);
        return;
      }
      case 'ask': {
        const actionId = newActionId();
        run.actionId = actionId;
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
        const actionId = run.actionId ?? newActionId();
        const conversation = state.conversations.get(run.conversation_id)!;
        let cards = step.cards(actionId);
        cards = cards.map((card) => {
          if (card.type === 'GOAL_SAVED') {
            conversation.goalVersion += 1;
            const goal = { ...card.goal, version: conversation.goalVersion };
            conversation.goal = {
              action_id: actionId,
              version: goal.version,
              slide_number: goal.slide_number,
              checked: false,
            };
            return { ...card, goal };
          }
          if (card.type === 'MEMORY_SAVED') {
            const item: ProfileItem = { item_id: newItemId(), key: card.key, value: card.value };
            state.memory.items = [...state.memory.items.filter((i) => i.key !== item.key), item];
            return { ...card, item_id: item.item_id };
          }
          if (card.type === 'GOAL_PROPOSAL') return { ...card, proposal_id: newProposalId() };
          return card;
        });
        return finish(state, run, step.status, cards);
      }
    }
  }

  function failRun(
    state: UserState,
    run: Run,
    status: 'FAILED' | 'PARTIAL',
    code: RunErrorCode | null,
  ) {
    run.error = code ? { code } : null;
    // 12-3: 실행 오류가 붐빔이면 5초 뒤 다시
    run.retry_after_s = code === 'AGENT_BUSY' ? 5 : null;
    finish(state, run, status, []);
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

  /** 내 피치인지 — 없거나 남의 대상은 같은 404 (12-3). 목의 피치는 하나입니다 */
  const knownScope = (pitchId: unknown) => pitchId === null || pitchId === PITCH_ID;

  /** 이 대화에 저장된 제안 카드의 그 안 — 없거나 다른 대화의 것이면 null (12-3 → 404) */
  function proposalOption(
    conversation: Conversation,
    choice: NonNullable<SendMessageRequest['choice']>,
  ) {
    for (const message of conversation.messages)
      for (const card of message.cards ?? [])
        if (card.type === 'GOAL_PROPOSAL' && card.proposal_id === choice.proposal_id)
          return card.options.find((o) => o.label === choice.option) ?? null;
    return null;
  }

  const activeRun = (state: UserState, conversationId: string) =>
    [...state.runs.values()].find(
      (r) => r.conversation_id === conversationId && !isTerminalRun(r.status),
    ) ?? null;

  const summary = (c: Conversation): ConversationSummary => ({
    conversation_id: c.conversation_id,
    title: c.title,
    scope_pitch_id: c.scope_pitch_id,
    scope_pitch_title: c.scope_pitch_title,
    last_user_activity_at: c.last_user_activity_at,
    created_at: c.created_at,
  });

  function withUser(fn: (state: UserState, info: Info) => Response | Promise<Response>) {
    return (info: Info) => {
      // 스위치가 꺼지면 대화 API 가 AGENT_DISABLED 를 돌려준다 (12-3) — 화면은 버튼을 숨깁니다
      if (availability === 'off') return error('AGENT_DISABLED', 503);
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
        const conversations = [...state.conversations.values()]
          .filter((c) => !pitchId || c.scope_pitch_id === pitchId)
          .map(summary)
          .sort((a, b) => b.last_user_activity_at.localeCompare(a.last_user_activity_at));
        return HttpResponse.json({ conversations });
      }),
    ),

    http.post(
      `*${chatPaths.conversations}`,
      withUser(async (state, { request }) => {
        // BE `CreateConversationRequest` 는 `pitch_id` 만 받습니다. `memory_off` 는 (BE 미구현)이라 목만 받습니다
        const body = (await request.json().catch(() => null)) as {
          pitch_id?: unknown;
          memory_off?: unknown;
        } | null;
        const pitchId = body?.pitch_id ?? null;
        if (
          !isBody(body) ||
          !onlyKeys(body, ['pitch_id', 'memory_off']) ||
          !uuidOrNull(pitchId) ||
          !(body.memory_off === undefined || typeof body.memory_off === 'boolean')
        )
          return error('VALIDATION_ERROR', 422);
        if (!knownScope(pitchId)) return error('NOT_FOUND', 404);
        const conversation = createConversation(
          state,
          pitchId as string | null,
          '',
          body.memory_off === true,
        );
        return HttpResponse.json(summary(conversation), { status: 201 });
      }),
    ),

    http.get(
      `*${chatPaths.messages(':conversationId')}`,
      withUser((state, { params, request }) => {
        const conversation = state.conversations.get(String(params.conversationId));
        if (!conversation) return error('NOT_FOUND', 404);
        // 최근부터 한 페이지 — 더 앞은 before_seq 로 (BE `MessageListResponse`)
        const query = new URL(request.url).searchParams;
        const beforeSeq = Number(query.get('before_seq') ?? Infinity);
        const limit = Math.min(Number(query.get('limit') ?? 30), 50);
        if (!(beforeSeq >= 1) || !Number.isInteger(limit) || limit < 1)
          return error('VALIDATION_ERROR', 422);
        const all = withSupersededBy(conversation.messages).map((msg, i) => ({
          ...msg,
          seq: i + 1,
        }));
        const earlier = all.filter((msg) => msg.seq < beforeSeq);
        const page = earlier.slice(-limit);
        return HttpResponse.json({
          messages: page,
          next_before_seq: earlier.length > page.length ? page[0]!.seq : null,
        });
      }),
    ),

    // 대화의 마지막 실행 — 끝났어도 돌려주고, 한 번도 없으면 null (BE `latest_run_status`).
    // 조회만 하고 실행을 나아가게 하지 않습니다(나아가는 것은 실행 조회 GET 입니다)
    http.get(
      `*${chatPaths.conversationRun(':conversationId')}`,
      withUser((state, { params }) => {
        const conversation = state.conversations.get(String(params.conversationId));
        if (!conversation) return error('NOT_FOUND', 404);
        const runs = [...state.runs.values()].filter(
          (r) => r.conversation_id === conversation.conversation_id,
        );
        const last = runs.at(-1);
        return HttpResponse.json(last ? runView(last, 0) : null);
      }),
    ),

    http.patch(
      `*${chatPaths.scope(':conversationId')}`,
      withUser(async (state, { params, request }) => {
        const conversation = state.conversations.get(String(params.conversationId));
        if (!conversation) return error('NOT_FOUND', 404);
        if (activeRun(state, conversation.conversation_id))
          return error('AGENT_RUN_IN_PROGRESS', 409);
        // BE `ChangeScopeRequest` — `pitch_id` 는 빠지면 안 되고(전체는 null), 다른 칸은 받지 않습니다
        const body: unknown = await request.json().catch(() => null);
        if (!isBody(body) || !('pitch_id' in body) || !onlyKeys(body, ['pitch_id']))
          return error('VALIDATION_ERROR', 422);
        const pitchId = body.pitch_id;
        if (!uuidOrNull(pitchId)) return error('VALIDATION_ERROR', 422);
        if (!knownScope(pitchId)) return error('NOT_FOUND', 404);
        conversation.scope_pitch_id = pitchId ?? null;
        conversation.scope_pitch_title = pitchId ? pitch.pitch_title : null;
        return HttpResponse.json(summary(conversation));
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
          const code = sendFailure;
          const status = SEND_FAILURE_STATUS[code];
          sendFailure = null;
          if (code === 'AGENT_BUSY') return error(code, status, { 'Retry-After': '5' });
          if (code === 'AGENT_DAILY_LIMIT')
            return error(code, status, { 'Retry-After': String(secondsToSeoulMidnight()) });
          return error(code, status);
        }
        // 고르기(choice) · 다시 묻기는 그 제안 · 메시지가 있는 대화에서만 옵니다 — 빈 대화를 만들기 전에 거릅니다
        if ((body.choice || body.again_of_message_id) && !body.conversation_id)
          return error('NOT_FOUND', 404);
        let conversation: Conversation | undefined;
        if (body.conversation_id) {
          conversation = state.conversations.get(body.conversation_id);
          if (!conversation) return error('NOT_FOUND', 404);
        } else {
          if (!knownScope(body.scope_pitch_id)) return error('NOT_FOUND', 404);
          conversation = createConversation(state, body.scope_pitch_id, body.text, false);
        }
        const chosen = body.choice ? proposalOption(conversation, body.choice) : null;
        if (body.choice && !chosen) return error('NOT_FOUND', 404);
        // 잴 수 없는 안(goal null)은 화면에 버튼이 없어 고를 수 없습니다 — 문서가 정하지 않아 목은 422 로 둡니다
        if (chosen && !chosen.goal) return error('VALIDATION_ERROR', 422);
        // 다시 묻기는 이 대화에 있는 메시지만 — 없거나 남의 것은 같은 404 (12-3)
        const again = body.again_of_message_id;
        if (again && !conversation.messages.some((msg) => msg.message_id === again))
          return error('NOT_FOUND', 404);
        const running = activeRun(state, conversation.conversation_id);
        if (running?.status === 'WAITING_APPROVAL')
          settleAsk(state, running, 'CANCELLED', '새 메시지를 받아 앞의 확인 요청은 취소했어요.');
        else if (running) return error('AGENT_RUN_IN_PROGRESS', 409);

        if (!conversation.title) conversation.title = [...body.text].slice(0, 30).join('');
        const message = userMessage(body.text);
        conversation.messages.push(message);
        conversation.last_user_activity_at = message.created_at;
        const run: Run = {
          run_id: newRunId(),
          conversation_id: conversation.conversation_id,
          status: 'QUEUED',
          steps: scenario(body.text, {
            memoryOn: state.memory.enabled && !conversation.memory_off,
            chosen,
            screen: body.page.screen,
            assessmentId: newAssessmentId(),
            priorAssessmentId:
              conversation.messages.flatMap((msg) => msg.assessments ?? []).at(-1)?.assessment_id ??
              null,
          }),
          events: [],
          plan: [],
          trace: [],
          claims: [],
          cards: [],
          error: null,
          retry_after_s: null,
          ask: null,
          failure: runFailure,
          // 고르기는 승인 없이 바로 실행하는 목표 저장 — 그 행동의 ID 를 접수 때 정합니다
          actionId: chosen ? newActionId() : null,
          chosen,
          queuedShown: false,
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
        // [취소]는 새 메시지가 아니라서 "새 메시지를 받아 …" 문장을 붙이지 않습니다.
        // 승인 대기 취소는 (BE 미구현) — 문서(L2-41 · L2-42)대로 취소 문장 메시지를 남깁니다
        if (run.status === 'WAITING_APPROVAL') settleAsk(state, run, 'CANCELLED', null);
        else if (!isTerminalRun(run.status)) {
          // BE `cancel_run` 처럼 — 오류 코드만 남기고 답 메시지는 만들지 않습니다
          run.status = 'CANCELLED';
          run.cards = [];
          run.ask = null;
          run.error = { code: 'AGENT_CANCELLED' };
        }
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
      `*${chatPaths.undo(':runId', ':actionId')}`,
      withUser((state, { params }) => {
        const actionId = String(params.actionId);
        const runId = String(params.runId);
        const again = state.undone.get(actionId);
        // 같은 실행 경로로 다시 되돌리면 처음 결과 그대로 — 다른 실행의 경로는 그 행동을 찾지 못합니다
        if (again?.run_id === runId) return HttpResponse.json(again);
        const conversation = [...state.conversations.values()].find((c) =>
          c.messages.some(
            (msg) =>
              msg.run_id === runId &&
              (msg.cards ?? []).some(
                (card) => card.type === 'GOAL_SAVED' && card.action_id === actionId,
              ),
          ),
        );
        // 없거나 남의 대상은 같은 404, 그 뒤 다른 목표가 저장됐으면 409 (12-3)
        if (!conversation) return error('NOT_FOUND', 404);
        if (conversation.goal?.action_id !== actionId) return error('AGENT_STATE_INVALID', 409);
        conversation.goalVersion += 1;
        conversation.goal = null;
        // 처음 저장을 되돌렸으니 목표 내용이 없습니다(BE `GoalUndoneResponse.goal` null)
        const result: UndoResponse = {
          run_id: runId,
          action_id: actionId,
          goal_version: conversation.goalVersion,
          goal: null,
        };
        state.undone.set(actionId, result);
        conversation.messages.push({ ...userMessage('목표를 되돌렸어요.'), role: 'system' });
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
      scope_pitch_title: scope === PITCH_ID ? pitch.pitch_title : null,
      last_user_activity_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      memory_off: memoryOff,
      goalVersion: 0,
      goal: null,
      messages: [],
    };
    state.conversations.set(conversation.conversation_id, conversation);
    return conversation;
  }

  return { handlers, controls };
}

/** 근거 표시 — 그 Take 를 읽은 답 (6-8 `basis`). 배지 글자는 화면이 이것으로 만듭니다 */
const basisOf = (takeNumber: number): MessageBasis => ({
  pitches: [
    {
      pitch_id: PITCH_ID,
      script_version_id: 'sv2',
      presentation_version_id: 'pv2',
      standard_version: 4,
      takes: [
        {
          take_id:
            pitch.takes.find((t) => t.take_version === takeNumber)?.take_id ??
            `20000000-0000-4000-8000-${String(takeNumber).padStart(12, '0')}`,
          take_number: takeNumber,
        },
      ],
    },
  ],
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 판단마다 그 판단을 대체한 새 판단의 ID — 대화 전체의 `supersedes` 로 계산합니다 (12-3 메시지 목록) */
function withSupersededBy(messages: ChatMessage[]): ChatMessage[] {
  const by = new Map<string, string>();
  for (const message of messages)
    for (const a of message.assessments ?? [])
      for (const old of a.supersedes) by.set(old, a.assessment_id);
  return messages.map((message) => ({
    ...message,
    assessments:
      message.assessments?.map((a) => ({
        ...a,
        superseded_by: by.get(a.assessment_id) ?? null,
      })) ?? null,
  }));
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

type Body = Record<string, unknown>;

const isBody = (v: unknown): v is Body => typeof v === 'object' && v !== null && !Array.isArray(v);
/** BE 요청 DTO 는 모르는 칸을 받지 않습니다(`extra="forbid"`) */
const onlyKeys = (body: Body, keys: readonly string[]) =>
  Object.keys(body).every((k) => keys.includes(k));
/** UUID 칸 — 빠짐 · null 이거나 UUID 만 (12-3 "잘못된 UUID · 열거값은 저장 전 422") */
const uuidOrNull = (v: unknown): v is string | null | undefined =>
  v === undefined || v === null || (typeof v === 'string' && UUID.test(v));

const SEND_KEYS = [
  'conversation_id',
  'scope_pitch_id',
  'text',
  'page',
  'client_request_id',
  'again_of_message_id',
  'choice',
] as const;
const PAGE_KEYS = ['route', 'pitch_id', 'take_id', 'screen'] as const;

/**
 * 메시지 보내기 본문 검사 — BE `SendMessageRequest` 와 같은 규칙이라 BE 가 422 로 막을 요청은 목도 막습니다.
 * 공백뿐 · 2,000자 초과(앞뒤 공백 포함, 코드 포인트) · 모르는 칸 · 형식이 틀린 UUID · 1~64자를 벗어난 경로
 */
function parseSend(raw: string): SendMessageRequest | null {
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isBody(body) || !onlyKeys(body, SEND_KEYS)) return null;
  const text = typeof body.text === 'string' ? body.text : '';
  if (!text.trim() || codePoints(text) > 2000 || text.includes('\u0000')) return null;
  // BE 는 보내기 키를 UUID 로만 받습니다(`client_request_id: uuid.UUID`)
  if (typeof body.client_request_id !== 'string' || !UUID.test(body.client_request_id)) return null;
  if (![body.conversation_id, body.scope_pitch_id, body.again_of_message_id].every(uuidOrNull))
    return null;
  const page = body.page;
  if (
    !isBody(page) ||
    !onlyKeys(page, PAGE_KEYS) ||
    typeof page.route !== 'string' ||
    codePoints(page.route) < 1 ||
    codePoints(page.route) > 64 ||
    !uuidOrNull(page.pitch_id) ||
    !uuidOrNull(page.take_id) ||
    !(page.screen === undefined || page.screen === null || isBody(page.screen))
  )
    return null;
  const choice = body.choice ?? null;
  if (
    choice !== null &&
    !(
      isBody(choice) &&
      onlyKeys(choice, ['proposal_id', 'option']) &&
      typeof choice.proposal_id === 'string' &&
      UUID.test(choice.proposal_id) &&
      ['USER', 'EXPERT'].includes(String(choice.option))
    )
  )
    return null;
  return {
    conversation_id: typeof body.conversation_id === 'string' ? body.conversation_id : null,
    scope_pitch_id: typeof body.scope_pitch_id === 'string' ? body.scope_pitch_id : null,
    text,
    // 화면 상태가 빠졌으면 null — 타입과 런타임 값을 맞춥니다
    page: {
      route: page.route,
      pitch_id: typeof page.pitch_id === 'string' ? page.pitch_id : null,
      take_id: typeof page.take_id === 'string' ? page.take_id : null,
      screen: (page.screen ?? null) as SendMessageRequest['page']['screen'],
    } as SendMessageRequest['page'],
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
