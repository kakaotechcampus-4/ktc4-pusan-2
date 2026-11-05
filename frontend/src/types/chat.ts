/**
 * 대화형 코치 에이전트 — 화면 ↔ BE 계약 (잠정).
 *
 * 원본: `docs/ai/features/chat-agent.md` 5-2 · 5-5 · 12-3 · 12-4 (개정판 PR #900, #899).
 * 화면 ↔ BE 모양(경로 · 목록 키 · 칸 이름)은 **BE #933 `feat/be-chat-api-834` 55aa10d0 (`module/chat/api_dto.py`)** 에
 * 맞췄습니다 — 실행 이벤트 커서 · 승인 · 거절 · 최신 아님 사유까지 들어온 판입니다. BE 도 머지 전입니다.
 * BE 가 아직 열지 않은 것(기억 · '기억 없이')은 문서 계약대로 두고 `(BE 미구현)` 으로 표시했습니다.
 * 목(`mocks/chat.ts`)은 그것까지 문서대로 흉내 냅니다.
 *
 * AI 가 만드는 내용(문장 · 근거 · 판단 · 카드 · 할 일 · 도구 기록 · 화면 상태)은 AI 연구 모듈의
 * `ai/research/chat-agent/src/chat_agent/schemas.py` 에 맞췄습니다 — 기준: `feat/ai-chat-agent-svc-guide-facts`
 * `8ed5a688` (#921, 머지 전). 문서 5-2 표보다 코드가 넓은 곳(`| null`)은 받는 쪽이라 코드를 따릅니다.
 *
 * BE 그대로 snake_case 입니다. 없음은 `?` 가 아니라 `| null` 입니다(frontend/AGENTS.md 6절).
 */

// ── 실행 · 상태 ───────────────────────────────────────────────

/** 실행 상태 (12-3 상태 전이). 종결 상태에서는 바뀌지 않습니다 */
export type RunStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'DONE'
  | 'PARTIAL'
  | 'REFUSED'
  | 'FAILED'
  | 'CANCELLED'
  | 'EXPIRED';

/** 행동 상태 (12-3). `ASK` 행동은 PENDING 에서 승인 · 거절 · 만료 · 취소로 갈립니다 */
export type ActionStatus =
  'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'REJECTED' | 'EXPIRED' | 'CANCELLED';

/** 할 일 한 줄 (5-2 `PlanStep`) */
export interface PlanStep {
  /** 1~60자 */
  step: string;
  status: 'TODO' | 'DOING' | 'DONE' | 'SKIPPED';
  /** ≤200자 */
  note: string | null;
}

/** 도구 기록 한 줄 — 화면의 "확인한 것" (5-2 `TraceItem`) */
export interface TraceItem {
  seq: number;
  tool: string;
  args_summary: string;
  result_summary: string;
  ms: number;
  outcome: 'OK' | 'EMPTY' | 'ERROR' | 'CACHED';
}

// ── 답 문장 · 근거 ─────────────────────────────────────────────

/**
 * 근거 값 (5-2 `Evidence`). `RECORD` 는 내 기록, `SCREEN` 은 보낸 화면 상태, `COMPUTED` 는 코드가 계산한 값입니다.
 * `COMPUTED` 의 입력 근거(`inputs`)도 같은 목록에 함께 옵니다 — 칩은 입력 기록으로 그립니다(12-4 F4).
 */
export interface Evidence {
  evidence_id: string;
  origin: 'RECORD' | 'SCREEN' | 'COMPUTED';
  /** AI 코드는 null 을 허용합니다 — 아직 피치가 없는 새 피치 화면의 값 (문서 5-2 표는 null 없음) */
  pitch_id: string | null;
  take_id: string | null;
  take_number: number | null;
  kind: string | null;
  revision: number | null;
  path: string;
  metric: string | null;
  slide_number: number | null;
  value: number | string;
  unit: string | null;
  rule_version: string | null;
  saved: boolean | null;
  screen_snapshot_id: string | null;
  inputs: string[] | null;
  calc: string | null;
}

/** 문장 종류. `GENERAL` 은 기록 · 자료로 확인하지 않은 일반 조언이라 칩이 없습니다(6-10) */
export type ClaimKind = 'RECORD' | 'GUIDE' | 'GENERAL' | 'ACTION_RESULT' | 'SYSTEM' | 'ASSESSMENT';

/** 답 문장 하나 (5-2 `Claim`). 화면은 고치거나 다시 계산하지 않습니다 */
export interface Claim {
  kind: ClaimKind;
  assessment_id: string | null;
  /** 1~300자 */
  text: string;
  evidence_ids: string[];
  source_ids: string[];
  action_id: string | null;
}

/** 발표법 · 사용법 자료 (5-2 `GuideCard`). 자료 칩을 펼치면 인용을 보입니다 */
export interface GuideCard {
  source_id: string;
  title: string;
  /** ≤300자 */
  quote: string;
  source: string;
  license: string;
}

export type Verdict = 'SUPPORTED' | 'PARTLY' | 'CONTRADICTED' | 'UNVERIFIABLE';

/**
 * 사용자 주장 판단 (5-2 `Assessment`). 화면이 쓰는 칸만 둡니다 —
 * `claim_key` · `evidence_basis` 같은 대조용 칸은 BE · AI 가 씁니다.
 */
export interface Assessment {
  assessment_id: string;
  /** 사용자 말 원문의 부분 문자열 */
  quote: string;
  type: 'MEASURED' | 'PREFERENCE' | 'KNOWLEDGE' | 'REQUEST';
  verdict: Verdict;
  evidence_ids: string[];
  source_ids: string[];
  reason: string;
  supersedes: string[];
}

/** 메시지 목록의 판단 — 새 판단이 이 판단을 대체했으면 그 ID (12-3 메시지 목록) */
export interface ListedAssessment extends Assessment {
  superseded_by: string | null;
}

// ── 목표 · 카드 ───────────────────────────────────────────────

/** 대화 목표 (5-2 `Goal`). 화면은 지표 · 장 · 비교 방법 · 기준만 보입니다 */
export interface Goal {
  version: number;
  pitch_id: string;
  created_at: string;
  metric: string;
  slide_number: number | null;
  operator: 'gt_base' | 'lt_base' | 'in_range';
  script_version_id: string | null;
  standard_version: number | null;
  goal_rule_version: string;
  base: {
    take_id: string;
    take_number: number;
    kind: string;
    revision: number;
    value: number;
    metric_rule_version: string;
    range: { low: number | null; high: number | null } | null;
  };
  min_delta: number | null;
  status: 'OPEN' | 'ACHIEVED' | 'NOT_ACHIEVED' | 'BASELINE_MISMATCH';
}

export type ProposedGoal = Omit<Goal, 'version' | 'status' | 'created_at'>;

/** 리뷰 결과 한쪽 — 바꾸기 전(A) · 참고 결과(B)가 같은 모양입니다 */
export interface ReassessSide {
  score: number | null;
  /** 차원 상태. 문서 12-4 는 null 을 "판정 없음" 으로 보이고, AI 코드는 null 을 받지 않습니다 — 화면은 둘 다 받습니다 */
  dimensions: Record<string, string | null>;
  /** 사용자 기준 판정 목록 (AI 코드 `list[dict]`) */
  criteria_results: Record<string, unknown>[];
}

/** 카드 (5-2 `Card` 종류). 모르는 `type` 은 화면이 대체 표시로 그립니다(12-4) */
export type Card =
  | {
      type: 'GOAL_SAVED';
      action_id: string;
      goal: Goal;
      reason: string;
      evidence_ids: string[];
      undo: true;
    }
  | {
      /** 문서 5-2 의 `evidence_basis` 는 BE · AI 가 대조에 쓰는 칸이라 화면 타입에서 뺐습니다 */
      type: 'GOAL_PROPOSAL';
      proposal_id: string;
      options: {
        label: 'USER' | 'EXPERT';
        /** null 이면 잴 수 없는 안 — 설명만 보이고 [이 안으로] 버튼을 두지 않습니다 (12-4, AI 코드 `GoalDraft | None`) */
        goal: ProposedGoal | null;
        reason: string;
        evidence_ids: string[];
        content_hash: string | null;
      }[];
    }
  | {
      type: 'REASSESS_RESULT';
      take_id: string;
      /** 무엇을 바꿔 다시 평가했는지 (AI 코드 `dict`) */
      change: Record<string, unknown>;
      before: ReassessSide;
      after: ReassessSide;
      changed_fields: string[];
      official_differs: boolean;
    }
  | { type: 'START_PRACTICE'; pitch_id: string }
  | {
      type: 'ASK_CONFIRM';
      action_id: string;
      action_type: 'SAVE_GOAL' | 'REASSESS';
      title: string;
      summary: string;
      before: Record<string, unknown> | null;
      after: Record<string, unknown>;
      notes: string[];
      expires_at: string;
      /** 승인 · 거절 요청에 그대로 보냅니다 */
      args_hash: string;
    }
  | {
      type: 'GOAL_RESULT';
      goal_version: number;
      take_id: string;
      /** 5-5 `goal_result.status`. `UNKNOWN` 만 끝난 판정이 아닙니다 */
      status: 'ACHIEVED' | 'NOT_ACHIEVED' | 'UNKNOWN' | 'BASELINE_MISMATCH';
      observed: number | null;
      reason: string;
    }
  | {
      type: 'MEMORY_SAVED';
      item_id: string;
      key: ProfileKey;
      value: string;
    };

export type CardType = Card['type'];

// ── 실행 이벤트 · 실행 조회 ────────────────────────────────────

/** `claim` 줄 — 검사를 통과한 문장 하나와 그 근거 · 자료 · 판단 (5-4) */
export interface ClaimEventData {
  /** 실행 전체에서 0부터 1씩. 이벤트 순번(`event_seq`)과 따로입니다 */
  index: number;
  claim: Claim;
  evidence: Evidence[];
  sources: GuideCard[];
  assessments: Assessment[];
}

/**
 * 실행 이벤트 (12-3 B15). BE 가 진행 줄 · 문장 줄을 저장하고 `after_seq` 커서로 돌려줍니다.
 * 저장된 이벤트의 순번 · 내용은 바뀌지 않습니다.
 */
export type RunEvent =
  | { event_seq: number; event: 'plan'; data: { plan: PlanStep[] }; masked: boolean }
  | { event_seq: number; event: 'tool_done'; data: { trace: TraceItem }; masked: boolean }
  /**
   * 문장 줄. 근거 피치 · Take 가 지워졌거나 남의 것이면 BE 가 `data: null, masked: true` 로 가립니다 —
   * 그 줄도 문장 순번 하나를 차지합니다(BE `RunEventDTO`)
   */
  | { event_seq: number; event: 'claim'; data: ClaimEventData | null; masked: boolean };

/** 실행 조회 응답 (12-3 '실행 상태 조회') */
export interface RunView {
  run_id: string;
  conversation_id: string;
  status: RunStatus;
  /** BE 는 아직 진행이 없거나 근거가 지워졌으면 null 을 줍니다 */
  plan: PlanStep[] | null;
  trace: TraceItem[] | null;
  /** `after_seq` 뒤의 이벤트, 순번순. 취소 · 승인 · 거절의 응답에는 싣지 않습니다(빈 목록) */
  events: RunEvent[];
  /** 다음 조회에 보낼 커서(받은 마지막 순번, 없으면 보낸 커서). 이벤트를 읽지 않은 응답은 null */
  next_after_seq: number | null;
  /** 크기 한도로 나눠 남은 이벤트가 있음 — 기다리지 않고 곧바로 다시 조회합니다 */
  has_more: boolean;
  /** 승인 대기 중인 확인 카드(ASK_CONFIRM). 그 밖에는 빈 목록 */
  cards: Card[];
  error: { code: ChatErrorCode } | null;
  retry_after_s: number | null;
}

// ── 메시지 · 대화 ─────────────────────────────────────────────

/** 최신 아님 사유 (6-8). 모르는 값은 "일부 기준이 바뀌었어요"로 보입니다 */
export type StaleReason =
  | 'NEW_TAKE'
  | 'RESULT_REVISED'
  | 'SCRIPT_VERSION_CHANGED'
  | 'CRITERIA_VERSION_CHANGED'
  | 'GOAL_CHANGED'
  | 'SOURCE_DELETED'
  | 'PRESENTATION_VERSION_CHANGED'
  | 'RULE_VERSION_CHANGED'
  | 'RESULT_UNAVAILABLE'
  | 'VERSION_UNKNOWN'
  | 'CORPUS_UPDATED';

/**
 * 근거 표시 (6-8 `basis`) — 이 답이 읽은 피치 · Take 와 그때의 버전. 배지 글자("Take 5 · 대본 V2 · 기준 v4 기준")는
 * 화면이 이것으로 만듭니다. BE 는 AI 가 준 값을 그대로 넘기므로(`Any`) 모르는 칸은 무시하고,
 * 피치를 읽지 않은 답은 `pitches` 가 빠져 올 수 있어 읽을 때 빈 목록으로 봅니다
 */
export interface MessageBasis {
  pitches: {
    pitch_id: string;
    script_version_id: string | null;
    presentation_version_id: string | null;
    standard_version: number | null;
    takes: { take_id: string; take_number: number }[];
  }[];
}

/** 메시지 목록 한 줄 (12-3 '메시지 목록', BE `ChatMessageResponse`) */
export interface ChatMessage {
  message_id: string;
  /** 대화 안 순번 — 앞 페이지를 읽는 커서(`before_seq`)로 씁니다 */
  seq: number;
  role: 'user' | 'assistant' | 'system';
  /** 사용자 말 · 시스템 문장. 에이전트 답은 `claims` 를 이은 값입니다 */
  text: string;
  /** 답을 만든 실행의 끝 상태. 사용자 · 시스템 메시지는 null */
  status: RunStatus | null;
  /** 이 메시지를 만든 실행. 사용자 · 시스템 메시지는 null */
  run_id: string | null;
  /** 최신 기준으로 다시 물은 메시지면 원래 메시지 */
  again_of_message_id: string | null;
  created_at: string;
  /** 사용자 · 시스템 메시지는 null 일 수 있습니다 */
  claims: Claim[] | null;
  cards: Card[] | null;
  assessments: ListedAssessment[] | null;
  evidence: Evidence[] | null;
  sources: GuideCard[] | null;
  basis: MessageBasis | null;
  plan: PlanStep[] | null;
  trace: TraceItem[] | null;
  /** 근거가 된 피치 · Take 가 지워져 본문과 근거를 가렸음(사유 `SOURCE_DELETED`) */
  masked: boolean;
  /** 6-8 최신 아님 사유. 모르는 값은 "일부 기준이 바뀌었어요" 로 보입니다(L3-27) */
  stale_reasons: string[];
}

/** 메시지 목록 한 페이지 — 최근부터, 더 앞은 `before_seq` 로 */
export interface MessagePage {
  messages: ChatMessage[];
  /** 더 앞의 메시지를 읽을 커서. 없으면 첫 메시지까지 다 읽었음 */
  next_before_seq: number | null;
}

/**
 * 대화 하나 (BE `ConversationResponse`) — 목록 · 만들기 · 범위 바꾸기가 같은 모양을 돌려줍니다.
 * "최신 아님 여부"(`stale`)는 목록 한 줄(`ConversationListItem`)에만 있습니다. '기억 없이' 표시는 (BE 미구현)
 */
export interface ConversationSummary {
  conversation_id: string;
  /** 첫 질문 앞부분 */
  title: string;
  /** 대화 범위. 전체면 null */
  scope_pitch_id: string | null;
  /** 범위 칩에 보일 발표 이름 */
  scope_pitch_title: string | null;
  last_user_activity_at: string;
  created_at: string;
}

/** 대화 목록 한 줄 (BE `ConversationListItem`) — 만들기 · 범위 바꾸기 응답에는 `stale` 이 없습니다 */
export interface ConversationListItem extends ConversationSummary {
  /** 마지막 답이 지금 기록과 달라졌음(6-8). 답이 없으면 false */
  stale: boolean;
}

export interface ConversationList {
  conversations: ConversationListItem[];
}

// ── 요청 ──────────────────────────────────────────────────────

/** 화면 상태 (12-4 `Screen`). 그 밖의 화면은 null 입니다 */
export type ChatScreen =
  | {
      name: 'pitch_create' | 'pitch_edit';
      snapshot_id: string;
      step: 'slides' | 'script' | 'info' | 'criteria';
      version_label: string | null;
      version_id: string | null;
      saved: {
        title: string;
        time_limit_sec: number;
        criteria_text: string;
        script_chars: number;
      } | null;
      draft: {
        title: string | null;
        slide_count: number | null;
        script_chars: number | null;
        time_limit_sec: number | null;
        criteria_text: string | null;
      };
      /** 저장값과 달라진 입력. 문서 12-4 의 세 값뿐입니다 — 슬라이드 · 대본은 `draft` 의 수로만 알립니다 */
      changed: ('title' | 'time_limit_sec' | 'criteria_text')[];
    }
  | {
      name: 'device_check';
      snapshot_id: string;
      camera: 'connected' | 'disconnected';
      mic: 'detected' | 'checking' | 'disconnected';
      gaze_setup: 'LOADING' | 'UNAVAILABLE' | 'IDLE' | 'RUNNING' | 'DONE' | 'FAILED';
      gaze_reason: string | null;
    };

/** 대화창을 연 화면 (5-3 `page`). 메시지마다 함께 보냅니다 */
export interface ChatPage {
  route: string;
  pitch_id: string | null;
  take_id: string | null;
  screen: ChatScreen | null;
}

/**
 * 새 대화 (BE `CreateConversationRequest`). `memory_off` 는 (BE 미구현) — 문서 12-3 "'기억 없이' 선택(대화를 만들 때만)".
 * 지금 BE 는 모르는 칸을 422 로 거절하므로(`extra="forbid"`), BE 가 칸을 열기 전에는 '기억 없이' 대화를 만들 수 없습니다
 */
export interface CreateConversationRequest {
  pitch_id: string | null;
}

/** (BE 미구현) '기억 없이' 새 대화 — 목만 받습니다. 지금 BE 에 보내면 422 입니다 */
export interface CreateMemoryOffConversationRequest extends CreateConversationRequest {
  memory_off: true;
}

export type CreateConversationResponse = ConversationSummary;

/** 메시지 보내기 (12-3). `client_request_id` 는 사용자 단위로 유일합니다 */
export interface SendMessageRequest {
  conversation_id: string | null;
  scope_pitch_id: string | null;
  /** 앞뒤 공백을 지운 1~2,000자(코드 포인트) */
  text: string;
  page: ChatPage;
  client_request_id: string;
  again_of_message_id: string | null;
  choice: { proposal_id: string; option: 'USER' | 'EXPERT' } | null;
}

/** 메시지 보내기 응답 — 202 */
export interface SendMessageAccepted {
  conversation_id: string;
  message_id: string;
  run_id: string;
  status: 'QUEUED';
}

export interface ScopeChangeRequest {
  pitch_id: string | null;
}

/** 범위 바꾸기 응답 — 바뀐 대화 그대로 */
export type ScopeChangeResponse = ConversationSummary;

/**
 * 승인 · 거절 (12-3, BE `ActionDecisionRequest`). 인자는 보내지 않고 화면에 보인 `args_hash` 만 보냅니다.
 * 응답은 이벤트 없이 실행 상태만인 `RunView` 입니다 — 승인이면 RUNNING, 거절이면 DONE.
 * 같은 결정을 다시 보내면 그때의 실행 상태를 줍니다
 */
export interface ActionDecisionRequest {
  decision: 'APPROVE' | 'REJECT';
  args_hash: string;
}

/** 되돌리기 응답 (BE #935 `GoalUndoneResponse`) */
export interface UndoResponse {
  run_id: string;
  action_id: string;
  /** 되돌려 새로 만든 목표 판 번호 */
  goal_version: number;
  /** 되살린 목표. 처음 저장을 되돌렸으면 null */
  goal: Goal | null;
}

// ── 기억 ──────────────────────────────────────────────────────

export type ProfileKey = 'TONE' | 'EXPLAIN_STYLE' | 'FOCUS_AREA' | 'CONTEXT';

/** 코칭 프로필 항목 하나 (5-2 `ProfileItem`) */
export interface ProfileItem {
  item_id: string;
  key: ProfileKey;
  /** 1~80자 */
  value: string;
}

/** (BE 미구현) 기억 설정 · 코칭 프로필 (12-3) */
export interface MemorySettings {
  enabled: boolean;
  items: ProfileItem[];
}

// ── 사용 가능 여부 · 오류 ──────────────────────────────────────

export interface ChatAvailability {
  enabled: boolean;
}

/** 화면이 받는 오류 코드 (5-9 · 12-4 F8). 모르는 코드는 "지금은 답을 만들지 못했어요"로 보입니다 */
export type ChatErrorCode =
  | 'VALIDATION_ERROR'
  | 'NOT_FOUND'
  | 'AGENT_DISABLED'
  | 'AGENT_BUSY'
  | 'AGENT_DAILY_LIMIT'
  | 'AGENT_STATE_INVALID'
  | 'AGENT_CONTEXT_TOO_LARGE'
  | 'AGENT_OUTPUT_TOO_LARGE'
  | 'AGENT_REQUEST_CONFLICT'
  | 'AGENT_RUN_IN_PROGRESS'
  | 'AGENT_CANCELLED'
  | 'AGENT_EXPIRED'
  | 'AGENT_TIMEOUT'
  | 'AGENT_UPSTREAM_ERROR'
  /** BE: 접수 중 DB 가 잠깐 안 됨(503) — 커밋 전 실패라 접수되지 않았습니다 */
  | 'AGENT_NOT_ACCEPTED';
