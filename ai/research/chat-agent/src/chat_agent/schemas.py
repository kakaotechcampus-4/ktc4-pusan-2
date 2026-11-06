"""`/agent/turn`의 입력 · 출력 모양 = 계약 (기능 문서 5절). 요청 · 응답 · 스트림 줄 · 실행 상태 외곽 ·
도구 · 행동 · 진단 기록 모양의 원본이다.

- 누가 쓰나: turn.py(요청 검사 · 줄 만들기) · loop.py · tools/ · actions.py · checks.py · state.py · 계약 시험 ·
  가짜 BE(chat_agent_lab/fake_be.py). BE는 서비스 탑재 뒤 같은 모양을 예시 JSON(examples/)으로 시험한다.
- 입력 모델(`_In`)은 모르는 필드를 무시한다(`architecture.md` 2절). 이 기능이 정하는 판별 값(`trigger` ·
  행동 `type` · 결과 `status` · 화면 `name` 등)은 `Literal`이라 모르는 값이면 422 다(5절 첫 문단).
  BE가 싣는 과거 기록의 상태 값(`TakeSummary.status` · `end_reason` · `script_mode` 등)은 `str`로 받아
  모르는 값을 '알 수 없음'으로 읽는다(그 판단은 values.py · claims.py).
- 카운터 · 판 번호는 엄격한 비음수 정수(`StrictInt`, bool 거절)다(8-3, L1-04).
- 출력 모델(`_Out`)은 정해진 필드만 낸다. `FinalData`는 상태 · 행동 · state 조합을 검사한다(5-5).
- 글자 수는 Python `len`(코드 포인트 수) 기준(5-2).
- 고칠 때: 필드를 바꾸면 기능 문서 5절 · examples/ · 계약 시험(tests/unit/test_contract_*.py)을 같은 PR에서
  고친다. 이름 변경 · 삭제 · 뜻 변경은 `version.SCHEMA_VERSION`을 올린다.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

NonNegInt = Annotated[StrictInt, Field(ge=0)]
PosInt = Annotated[StrictInt, Field(ge=1)]
Number = StrictInt | StrictFloat


class _In(BaseModel):
    """입력: 모르는 필드는 무시"""

    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    """출력: 정해진 필드만"""

    model_config = ConfigDict(extra="forbid")


def _finite(v: float | int | None) -> float | int | None:
    if v is not None and isinstance(v, float) and not math.isfinite(v):
        raise ValueError("유한한 수가 아니다")
    return v


# ── 5-2 공통 타입 ──────────────────────────────────────────────────────────


class LastReview(_In):
    take_id: StrictStr
    take_number: NonNegInt
    score: StrictInt | None = None
    priority_dimensions: list[StrictStr] = Field(default_factory=list)


class PitchSummary(_In):
    pitch_id: StrictStr
    title: StrictStr
    take_count: NonNegInt
    last_practiced_at: StrictStr | None = None
    last_review: LastReview | None = None


class TakeReviewSummary(_In):
    revision: NonNegInt
    score: StrictInt | None = None
    #: {차원: 상태}. 모르는 상태 값은 '알 수 없음'으로 읽는다
    dimensions: dict[StrictStr, StrictStr] = Field(default_factory=dict)


class TakeSummary(_In):
    take_id: StrictStr
    take_number: NonNegInt
    #: BE 기록의 상태 값(모르는 값은 알 수 없음으로 읽음)
    status: StrictStr
    mode: StrictStr | None = None
    script_mode: StrictStr | None = None
    end_reason: StrictStr | None = None
    #: 12-3 규칙으로 BE가 계산. null · false 인 Take는 비교 · 목표 확인에 쓰지 않는다
    completed_fully: StrictBool | None = None
    script_version_id: StrictStr | None = None
    presentation_version_id: StrictStr | None = None
    standard_version: StrictInt | None = None
    completed_at: StrictStr | None = None
    review: TakeReviewSummary | None = None


class TakeResults(_In):
    take_result: dict[str, Any] | None = None
    script_coverage: dict[str, Any] | None = None
    review: dict[str, Any] | None = None


class TakeRevisions(_In):
    TAKE_RESULT: NonNegInt | None = None
    SCRIPT_COVERAGE: NonNegInt | None = None
    REVIEW: NonNegInt | None = None


class TakeSnapshot(_In):
    summary: TakeSummary
    #: 저장된 payload 그대로(리뷰 요청용 축약 모델로 거르지 않음)
    results: TakeResults
    revisions: TakeRevisions
    #: 크기 때문에 뺀 경로(6-12). 이 아래 경로를 읽으면 OMITTED
    omitted_paths: list[StrictStr] = Field(default_factory=list)


GoalOperator = Literal["gt_base", "lt_base", "in_range"]
GoalStatus = Literal["OPEN", "ACHIEVED", "NOT_ACHIEVED", "BASELINE_MISMATCH"]


class Range(_In):
    low: Number | None = None
    high: Number | None = None

    _fin = field_validator("low", "high")(classmethod(lambda cls, v: _finite(v)))

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("범위의 low 가 high 보다 크다")
        return self


class GoalBase(_In):
    take_id: StrictStr
    take_number: NonNegInt
    kind: StrictStr
    revision: NonNegInt
    value: Number
    metric_rule_version: StrictStr
    range: Range | None = None

    _fin = field_validator("value")(classmethod(lambda cls, v: _finite(v)))


class GoalDraft(_In):
    """Goal 에서 version · status · created_at 을 뺀 모양(SAVE_GOAL 인자 · 목표 제안)."""

    pitch_id: StrictStr
    metric: StrictStr
    slide_number: PosInt | None = None
    operator: GoalOperator
    script_version_id: StrictStr | None
    standard_version: StrictInt | None
    goal_rule_version: StrictStr
    base: GoalBase
    min_delta: Number | None = None

    @model_validator(mode="after")
    def _delta_rule(self) -> GoalDraft:
        if self.operator == "in_range":
            if self.min_delta is not None:
                raise ValueError("in_range 목표의 min_delta 는 null 이다")
        else:
            d = self.min_delta
            if d is None or isinstance(d, bool) or not math.isfinite(float(d)) or d <= 0:
                raise ValueError("gt_base · lt_base 목표의 min_delta 는 유한 양수다")
        return self


class Goal(GoalDraft):
    version: NonNegInt
    created_at: StrictStr
    status: GoalStatus


PlanStatus = Literal["TODO", "DOING", "DONE", "SKIPPED"]


class PlanStep(_Out):
    step: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    status: PlanStatus
    note: Annotated[StrictStr, Field(max_length=200)] | None = None


TraceOutcome = Literal["OK", "EMPTY", "ERROR", "CACHED"]


class TraceItem(_Out):
    seq: PosInt
    tool: StrictStr
    args_summary: Annotated[StrictStr, Field(max_length=200)]
    result_summary: Annotated[StrictStr, Field(max_length=200)]
    ms: NonNegInt
    outcome: TraceOutcome


EvidenceOrigin = Literal["RECORD", "SCREEN", "COMPUTED"]


class Evidence(_Out):
    evidence_id: StrictStr
    origin: EvidenceOrigin
    pitch_id: StrictStr | None
    take_id: StrictStr | None = None
    take_number: NonNegInt | None = None
    kind: StrictStr | None = None
    revision: NonNegInt | None = None
    path: StrictStr
    metric: StrictStr | None = None
    slide_number: PosInt | None = None
    value: Number | StrictStr
    unit: StrictStr | None = None
    rule_version: StrictStr | None = None
    saved: StrictBool | None = None
    screen_snapshot_id: StrictStr | None = None
    inputs: list[StrictStr] | None = None
    calc: StrictStr | None = None

    @model_validator(mode="after")
    def _origin_rules(self) -> Evidence:
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("근거 값은 유한한 수다")
        if self.origin == "SCREEN":
            if not self.screen_snapshot_id or self.revision is not None:
                raise ValueError("SCREEN 근거는 screen_snapshot_id 필수, revision null")
        elif self.screen_snapshot_id is not None or self.saved is not None:
            raise ValueError("screen_snapshot_id · saved 는 SCREEN 근거만")
        if self.origin == "COMPUTED":
            if not self.inputs or not self.calc:
                raise ValueError("COMPUTED 근거는 inputs · calc 필수")
        elif self.inputs is not None or self.calc is not None:
            raise ValueError("inputs · calc 는 COMPUTED 근거만")
        return self


ClaimKind = Literal["RECORD", "GUIDE", "ACTION_RESULT", "SYSTEM", "ASSESSMENT"]


class Claim(_Out):
    kind: ClaimKind
    assessment_id: StrictStr | None = None
    text: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    source_ids: list[StrictStr] = Field(default_factory=list)
    action_id: StrictStr | None = None


class GuideCard(_Out):
    source_id: StrictStr
    title: StrictStr
    quote: Annotated[StrictStr, Field(max_length=300)]
    source: StrictStr
    license: StrictStr


AssessmentType = Literal["MEASURED", "PREFERENCE", "KNOWLEDGE", "REQUEST"]
Speaker = Literal["USER", "OTHER"]
ClaimOp = Literal["OVERALL", "ISSUE", "VALUE", "CHANGE"]
Relation = Literal["ALL", "SOME", "COMPARE"]
Polarity = Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
Verdict = Literal["SUPPORTED", "PARTLY", "CONTRADICTED", "UNVERIFIABLE"]
VerdictSource = Literal["CODE", "MODEL"]


class Subject(BaseModel):
    #: 앞 판단(history.prior_assessments)으로 다시 들어오므로 입력 규칙(모르는 필드 무시)을 따른다
    model_config = ConfigDict(extra="ignore")

    dimension: StrictStr | None = None
    metric: StrictStr | None = None
    slide_number: PosInt | None = None
    take_ids: list[StrictStr] = Field(default_factory=list)
    relation: Relation | None = None


class BasisTake(BaseModel):
    #: 앞 판단(history.prior_assessments)으로 다시 들어오므로 입력 규칙(모르는 필드 무시)을 따른다
    model_config = ConfigDict(extra="ignore")

    take_id: StrictStr
    results: dict[StrictStr, Any] = Field(default_factory=dict)
    rule_versions: dict[StrictStr, Any] = Field(default_factory=dict)


class BasisRecord(BaseModel):
    #: 앞 판단(history.prior_assessments)으로 다시 들어오므로 입력 규칙(모르는 필드 무시)을 따른다
    model_config = ConfigDict(extra="ignore")

    pitch_id: StrictStr
    script_version_id: StrictStr | None = None
    presentation_version_id: StrictStr | None = None
    standard_version: StrictInt | None = None
    takes: list[BasisTake] = Field(default_factory=list)


class BasisSource(BaseModel):
    #: 앞 판단(history.prior_assessments)으로 다시 들어오므로 입력 규칙(모르는 필드 무시)을 따른다
    model_config = ConfigDict(extra="ignore")

    source_id: StrictStr
    corpus_version: StrictStr


class BasisScreen(BaseModel):
    #: 앞 판단(history.prior_assessments)으로 다시 들어오므로 입력 규칙(모르는 필드 무시)을 따른다
    model_config = ConfigDict(extra="ignore")

    snapshot_id: StrictStr


class EvidenceBasis(BaseModel):
    """판단 · 제안이 기댄 원천별 버전 지문(5-2). 코드가 채운다. 입력으로도 들어오므로 모르는 필드는 무시."""

    model_config = ConfigDict(extra="ignore")

    records: list[BasisRecord] = Field(default_factory=list)
    sources: list[BasisSource] = Field(default_factory=list)
    screens: list[BasisScreen] = Field(default_factory=list)
    fingerprint: StrictStr


class Assessment(BaseModel):
    """사용자 말 속 주장 · 요청 하나의 판단(5-2 · 6-3). 앞 판단(history.prior_assessments)으로 다시 들어오므로
    모르는 필드는 무시한다(BE가 선택 필드를 먼저 더해도 요청이 깨지지 않게, `architecture.md` 2절)."""

    model_config = ConfigDict(extra="ignore")

    assessment_id: StrictStr
    quote: StrictStr
    type: AssessmentType
    speaker: Speaker
    speaker_label: StrictStr | None = None
    claim_op: ClaimOp | None = None
    subject: Subject
    value: Number | None = None
    value_unit: StrictStr | None = None
    polarity: Polarity
    verdict: Verdict
    verdict_source: VerdictSource
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    source_ids: list[StrictStr] = Field(default_factory=list)
    reason: StrictStr
    claim_key: StrictStr
    claim_link_key: StrictStr
    evidence_basis: EvidenceBasis
    supersedes: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _op_only_measured(self) -> Assessment:
        if self.type != "MEASURED" and self.claim_op is not None:
            raise ValueError("claim_op 는 MEASURED 만")
        return self


class SelectedProposal(_In):
    proposal_id: StrictStr
    option: Literal["USER", "EXPERT"]
    goal: GoalDraft
    content_hash: StrictStr
    evidence_basis: EvidenceBasis


CriteriaOperator = Literal["LT", "LTE", "GT", "GTE"]


class CriteriaTarget(_In):
    """리뷰 입력의 CriteriaTarget 과 같은 모양. 숫자 목표 셋은 모두 있거나 모두 없어야 한다."""

    display: StrictStr | None = None
    metric: StrictStr | None = None
    operator: CriteriaOperator | None = None
    value: Number | None = None

    @model_validator(mode="after")
    def _all_or_none(self) -> CriteriaTarget:
        parts = [self.metric is None, self.operator is None, self.value is None]
        if any(parts) and not all(parts):
            raise ValueError("metric · operator · value 는 모두 있거나 모두 없어야 한다")
        _finite(self.value)
        return self


# ── 카드 (5-2) ─────────────────────────────────────────────────────────────


class GoalSavedCard(_Out):
    type: Literal["GOAL_SAVED"] = "GOAL_SAVED"
    action_id: StrictStr
    goal: Goal
    reason: StrictStr
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    undo: Literal[True] = True


class GoalOption(_Out):
    label: Literal["USER", "EXPERT"]
    #: 잴 수 없는 사용자 안은 goal null(설명만, 선택 버튼 없음, 6-3)
    goal: GoalDraft | None
    reason: StrictStr
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    content_hash: StrictStr | None


class GoalProposalCard(_Out):
    type: Literal["GOAL_PROPOSAL"] = "GOAL_PROPOSAL"
    proposal_id: StrictStr
    options: Annotated[list[GoalOption], Field(min_length=1, max_length=2)]
    evidence_basis: EvidenceBasis


class ReassessSide(_Out):
    score: StrictInt | None
    dimensions: dict[StrictStr, StrictStr]
    criteria_results: list[dict[str, Any]]


class ReassessResultCard(_Out):
    type: Literal["REASSESS_RESULT"] = "REASSESS_RESULT"
    take_id: StrictStr
    change: dict[str, Any]
    before: ReassessSide
    after: ReassessSide
    changed_fields: list[StrictStr]
    official_differs: StrictBool


class StartPracticeCard(_Out):
    type: Literal["START_PRACTICE"] = "START_PRACTICE"
    pitch_id: StrictStr
    auto: StrictBool


class GoalResultCard(_Out):
    type: Literal["GOAL_RESULT"] = "GOAL_RESULT"
    goal_version: NonNegInt
    take_id: StrictStr
    status: Literal["ACHIEVED", "NOT_ACHIEVED", "UNKNOWN", "BASELINE_MISMATCH"]
    observed: Number | None
    reason: StrictStr


Card = Annotated[
    GoalSavedCard | GoalProposalCard | ReassessResultCard | StartPracticeCard | GoalResultCard,
    Field(discriminator="type"),
]


# ── 12-4 화면 상태 ───────────────────────────────────────────────────────────


class ScreenSaved(_In):
    title: StrictStr | None = None
    time_limit_sec: StrictInt | None = None
    criteria_text: StrictStr | None = None
    script_chars: StrictInt | None = None


class ScreenDraft(_In):
    title: StrictStr | None = None
    slide_count: StrictInt | None = None
    script_chars: StrictInt | None = None
    time_limit_sec: StrictInt | None = None
    criteria_text: Annotated[StrictStr, Field(max_length=2000)] | None = None


class PitchScreen(_In):
    name: Literal["pitch_create", "pitch_edit"]
    snapshot_id: StrictStr
    step: Literal["slides", "script", "info", "criteria"]
    version_label: StrictStr | None = None
    version_id: StrictStr | None = None
    saved: ScreenSaved | None = None
    draft: ScreenDraft
    changed: list[Literal["title", "time_limit_sec", "criteria_text"]] = Field(default_factory=list)


class DeviceScreen(_In):
    name: Literal["device_check"]
    snapshot_id: StrictStr
    camera: Literal["connected", "disconnected"]
    mic: Literal["detected", "checking", "disconnected"]
    gaze_setup: Literal["LOADING", "UNAVAILABLE", "IDLE", "RUNNING", "DONE", "FAILED"]
    gaze_reason: StrictStr | None = None


Screen = Annotated[PitchScreen | DeviceScreen, Field(discriminator="name")]


# ── 5-3 입력 ───────────────────────────────────────────────────────────────

Trigger = Literal["USER_MESSAGE", "ACTION_RESULT", "TAKE_REVIEWED"]


class Run(_In):
    run_id: StrictStr
    conversation_id: StrictStr
    user_id: StrictStr
    scope_pitch_id: StrictStr | None = None
    trigger: Trigger
    state_revision: NonNegInt


class Choice(_In):
    proposal_id: StrictStr
    option: Literal["USER", "EXPERT"]


class ReviewEvent(_In):
    take_id: StrictStr
    review_revision: NonNegInt
    goal_version: NonNegInt


class Page(_In):
    route: StrictStr
    pitch_id: StrictStr | None = None
    take_id: StrictStr | None = None
    screen: Screen | None = None


class HistoryMessage(_In):
    role: Literal["user", "assistant", "system"]
    text: Annotated[StrictStr, Field(max_length=4000)]
    truncated: StrictBool = False
    basis: dict[str, Any] | None = None
    stale_reasons: list[StrictStr] = Field(default_factory=list)


class History(_In):
    summary: Annotated[StrictStr, Field(max_length=4000)] | None = None
    messages: Annotated[list[HistoryMessage], Field(max_length=20)] = Field(default_factory=list)
    prior_assessments: Annotated[list[Assessment], Field(max_length=50)] = Field(
        default_factory=list
    )


class PitchInfo(_In):
    pitch_id: StrictStr
    title: StrictStr
    time_limit_ms: NonNegInt | None = None
    min_ms: NonNegInt | None = None
    max_ms: NonNegInt | None = None


class PitchVersions(_In):
    script_version_id: StrictStr | None = None
    script_version_label: StrictStr | None = None
    presentation_version_id: StrictStr | None = None
    page_count: StrictInt | None = None
    standard_version: StrictInt | None = None


class PitchCriteria(_In):
    standard_version: StrictInt | None = None
    display_criteria: list[StrictStr] = Field(default_factory=list)
    targets: Annotated[list[CriteriaTarget], Field(max_length=20)] = Field(default_factory=list)
    script_used: StrictBool | None = None


class PitchContext(_In):
    info: PitchInfo
    versions: PitchVersions
    takes: Annotated[list[TakeSummary], Field(max_length=10)] = Field(default_factory=list)
    focus_take: TakeSnapshot | None = None
    missions: list[dict[str, Any]] = Field(default_factory=list)
    criteria: PitchCriteria | None = None


class Context(_In):
    pitches: Annotated[list[PitchSummary], Field(max_length=20)] = Field(default_factory=list)
    pitches_total: NonNegInt = 0
    pitch: PitchContext | None = None
    conversation_goal: Goal | None = None
    goal_version: NonNegInt = 0
    loaded_takes: list[TakeSnapshot] = Field(default_factory=list)
    removed_sources: list[StrictStr] = Field(default_factory=list)
    selected_proposal: SelectedProposal | None = None


class SignedState(BaseModel):
    """실행 상태 외곽(5-6). v · kid · 서명 검사는 state.py 가 하고 틀리면 409(422 아님)."""

    model_config = ConfigDict(extra="ignore")

    v: Any = None
    kid: Any = None
    payload: Any = None
    signature: Any = None


ActionType = Literal["FETCH_PITCH", "FETCH_TAKE", "REASSESS", "SAVE_GOAL", "START_PRACTICE"]


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: StrictStr
    message: StrictStr = ""


class ActionResult(_In):
    action_id: StrictStr
    type: ActionType
    status: Literal["SUCCEEDED", "FAILED", "AMBIGUOUS"]
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None


class Resume(_In):
    #: 외곽 모양 검사는 state.verify 가 한다(모양이 틀려도 422 가 아니라 409 AGENT_STATE_INVALID, 5-6)
    state: Any
    action_results: list[ActionResult]


class Budget(_In):
    model_calls: NonNegInt
    tool_calls: NonNegInt
    elapsed_ms: NonNegInt
    input_tokens: NonNegInt
    output_tokens: NonNegInt


class TurnRequest(_In):
    run: Run
    user_message: StrictStr | None = None
    choice: Choice | None = None
    review_event: ReviewEvent | None = None
    page: Page
    history: History = Field(default_factory=History)
    context: Context
    resume: Resume | None = None
    budget_used: Budget

    @model_validator(mode="after")
    def _trigger_rules(self) -> TurnRequest:
        t = self.run.trigger
        if t == "USER_MESSAGE":
            msg = self.user_message
            if msg is None or not msg.strip():
                raise ValueError("USER_MESSAGE 는 공백이 아닌 user_message 가 필수다")
            if len(msg) > 2000:
                raise ValueError("user_message 는 2,000자 이하다")
            if self.resume is not None or self.review_event is not None:
                raise ValueError("USER_MESSAGE 에는 resume · review_event 가 없다")
            if self.choice is not None and self.context.selected_proposal is None:
                raise ValueError("choice 가 있으면 context.selected_proposal 이 필수다")
        else:
            if self.user_message is not None or self.choice is not None:
                raise ValueError("USER_MESSAGE 가 아니면 user_message · choice 는 null 이다")
            if t == "ACTION_RESULT" and (self.resume is None or self.review_event is not None):
                raise ValueError("ACTION_RESULT 는 resume 필수, review_event null")
            if t == "TAKE_REVIEWED" and (self.review_event is None or self.resume is not None):
                raise ValueError("TAKE_REVIEWED 는 review_event 필수, resume null")
        return self


# ── 5-8 행동 ───────────────────────────────────────────────────────────────

Autonomy = Literal["AUTO", "AUTO_UNDO", "ASK"]


class Action(_Out):
    action_id: StrictStr
    type: ActionType
    args: dict[str, Any]
    autonomy: Autonomy
    args_hash: StrictStr | None = None
    idempotency_key: StrictStr
    reason: StrictStr
    authorized_by: StrictStr | None = None


# ── 11-5 진단 기록 ──────────────────────────────────────────────────────────

DiagKind = Literal["MODEL", "TOOL", "CHECK", "ASSESS", "ACTION", "LIMIT"]
CodeEnum = Annotated[StrictStr, Field(pattern=r"^[A-Z_]{1,40}$")]


class ModelDetail(_Out):
    model: StrictStr | None = None
    success: StrictBool | None = None
    input_tokens: NonNegInt | None = None
    cached_input_tokens: NonNegInt | None = None
    output_tokens: NonNegInt | None = None
    finish_reason: Literal["STOP", "LENGTH", "TOOL_CALLS", "CONTENT_FILTER", "OTHER"] | None = None
    tool_call_count: NonNegInt | None = None
    error_code: CodeEnum | None = None
    fix_for: list[NonNegInt] | None = None


class ToolDetail(_Out):
    target_ids: list[StrictStr] | None = None
    revisions: dict[StrictStr, NonNegInt | None] | None = None
    outcome: TraceOutcome | None = None
    result_bytes: NonNegInt | None = None
    query_chars: NonNegInt | None = None
    error_code: CodeEnum | None = None


class CheckDetail(_Out):
    claim_index: NonNegInt | None = None
    claim_kind: ClaimKind | None = None
    field: Literal["cards", "plan", "trace", "history_summary"] | None = None
    round: Literal[0, 1] | None = None
    result: Literal["FIX_REQUESTED", "DROPPED", "ACTION_DROPPED"] | None = None
    # SUMMARY 만
    checked: NonNegInt | None = None
    failed: NonNegInt | None = None
    dropped: NonNegInt | None = None
    actions_dropped: NonNegInt | None = None


class AssessDetail(_Out):
    assessment_id: StrictStr
    type: AssessmentType
    claim_op: ClaimOp | None = None
    relation: Relation | None = None
    verdict_source: VerdictSource
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    supersedes: list[StrictStr] = Field(default_factory=list)
    prior_assessment_id: StrictStr | None = None
    basis_changed: StrictBool | None = None
    verdict_kept: StrictBool | None = None


class ActionDetail(_Out):
    action_id: StrictStr | None = None
    autonomy: Autonomy | None = None
    authorization: Literal["PASS", "FAIL", "NOT_REQUIRED"] | None = None
    result: Literal["PROPOSED", "DROPPED", "DOWNGRADED"]
    reason_code: CodeEnum | None = None


class LimitDetail(_Out):
    used: NonNegInt
    limit: NonNegInt
    attempted: NonNegInt | None = None


TOOL_NAMES = (
    "update_plan",
    "get_screen",
    "list_pitches",
    "list_takes",
    "get_take_report",
    "compare_takes",
    "get_goals",
    "search_guides",
)
CHECK_IDS = (
    "EVIDENCE_MISSING",
    "NUMBER_MISMATCH",
    "TARGET_MISMATCH",
    "QUOTE_MISMATCH",
    "STALE_EVIDENCE",
    "FORBIDDEN_CLAIM",
    "NUMBER_FORMAT",
    "CAUSAL_CLAIM",
    "ACTION_RESULT_UNVERIFIED",
    "SYSTEM_TEXT",
    "VERDICT_CONFLICT",
    "HEDGE_WORD",
    "AGREEMENT_WORD",
    "DISPLAY_TEXT",
    "PII",
    "AUTHORIZATION",
    "ACTION_ARGS",
    "FIX_NEW_CONTENT",
    "SCOPE_RULE",
)
LIMIT_CODES = (
    "MODEL_CALLS",
    "TOOL_CALLS",
    "TOOLS_PER_RESPONSE",
    "TURN_CALLS",
    "ACTIONS",
    "SAVE_ACTIONS",
    "REASSESS_PER_TURN",
    "PLAN_EDITS",
    "INPUT_TOKENS",
    "OUTPUT_RESERVE",
    "TURN_TIME",
    "CALL_TIME",
    "LINE_SIZE",
    "RESPONSE_SIZE",
    "STATE_SIZE",
)
#: 종류별 code 허용 목록(11-5). 진단 기록에 문장이 들어가지 않게 닫힌 목록만 받는다
DIAG_CODES: dict[str, tuple[str, ...]] = {
    "MODEL": ("RESEARCH", "ANSWER", "FIX"),
    "TOOL": TOOL_NAMES,
    "CHECK": (*CHECK_IDS, "SUMMARY"),
    "ASSESS": ("SUPPORTED", "PARTLY", "CONTRADICTED", "UNVERIFIABLE"),
    "ACTION": (
        "FETCH_PITCH",
        "FETCH_TAKE",
        "REASSESS",
        "SAVE_GOAL",
        "START_PRACTICE",
    ),
    "LIMIT": LIMIT_CODES,
}
_DETAIL_MODEL: dict[str, type[BaseModel]] = {
    "MODEL": ModelDetail,
    "TOOL": ToolDetail,
    "CHECK": CheckDetail,
    "ASSESS": AssessDetail,
    "ACTION": ActionDetail,
    "LIMIT": LimitDetail,
}


class DiagStep(_Out):
    seq: PosInt
    kind: DiagKind
    code: StrictStr
    start_ms: NonNegInt
    ms: NonNegInt | None = None
    parent_seq: PosInt | None = None
    detail: ModelDetail | ToolDetail | CheckDetail | AssessDetail | ActionDetail | LimitDetail

    @model_validator(mode="before")
    @classmethod
    def _detail_by_kind(cls, data: Any) -> Any:
        # 빈 detail({})은 여러 모델과 맞으므로 kind 로 먼저 고른다(줄이기 ① 뒤의 TOOL 단계, 11-5)
        if isinstance(data, dict) and data.get("kind") in _DETAIL_MODEL:
            det = data.get("detail")
            if isinstance(det, dict):
                data = {**data, "detail": _DETAIL_MODEL[data["kind"]].model_validate(det)}
        return data

    @model_validator(mode="after")
    def _detail_and_code_match_kind(self) -> DiagStep:
        if not isinstance(self.detail, _DETAIL_MODEL[self.kind]):
            raise ValueError(f"{self.kind} 단계의 detail 모양이 아니다")
        if self.code not in DIAG_CODES[self.kind]:
            raise ValueError(f"{self.kind} 단계의 code 가 허용 목록 밖이다")
        return self


class DiagVersions(_Out):
    feature_version: StrictStr
    prompt_version: StrictStr
    tool_schema: StrictStr
    corpus_version: StrictStr


class DiagEnd(_Out):
    status: StrictStr
    reason: Literal[
        "ANSWERED",
        "NEEDS_ACTION",
        "OUT_OF_SCOPE",
        "NO_EVIDENCE",
        "ALL_CLAIMS_DROPPED",
        "LIMIT",
        "LLM_ERROR",
        "CLIENT_DISCONNECTED",
        "OTHER",
    ]
    omitted_steps: NonNegInt = 0


class Diagnostics(_Out):
    v: Literal[1] = 1
    request_id: StrictStr
    run_id: StrictStr
    turn_call: PosInt
    versions: DiagVersions
    steps: list[DiagStep]
    end: DiagEnd
    truncated: StrictBool = False


# ── 5-5 최종 출력 ──────────────────────────────────────────────────────────

FinalStatus = Literal["DONE", "NEEDS_ACTION", "PARTIAL", "REFUSED"]


class Message(_Out):
    text: StrictStr
    claims: list[Claim]
    cards: list[Card] = Field(default_factory=list)
    sources: list[GuideCard] = Field(default_factory=list)

    @model_validator(mode="after")
    def _text_is_claims(self) -> Message:
        if self.text != "\n".join(c.text for c in self.claims):
            raise ValueError("message.text 는 claims[].text 를 LF 로 이은 값과 같아야 한다")
        return self


class BudgetOut(_Out):
    """`final.budget_used` — 5-3 `budget_used` 와 같은 다섯 카운터(BE가 다음 이어 부르기에 그대로 보낸다)."""

    model_calls: NonNegInt
    tool_calls: NonNegInt
    elapsed_ms: NonNegInt
    input_tokens: NonNegInt
    output_tokens: NonNegInt


class Usage(_Out):
    model_calls: NonNegInt
    tool_calls: NonNegInt
    elapsed_ms: NonNegInt
    input_tokens: NonNegInt | None
    output_tokens: NonNegInt | None
    measured: StrictBool
    missing_reason: StrictStr | None = None


class GoalResult(_Out):
    goal_version: NonNegInt
    take_id: StrictStr
    review_revision: NonNegInt
    status: Literal["ACHIEVED", "NOT_ACHIEVED", "UNKNOWN", "BASELINE_MISMATCH"]
    observed: Number | None
    reason: StrictStr
    terminal: StrictBool

    @model_validator(mode="after")
    def _terminal_rule(self) -> GoalResult:
        if (self.status == "UNKNOWN") == self.terminal:
            raise ValueError("UNKNOWN 만 terminal=false 다")
        return self


class Meta(_Out):
    schema_version: StrictStr
    feature_version: StrictStr
    model: StrictStr | None
    prompt_version: StrictStr
    corpus_version: StrictStr
    criteria_versions: dict[StrictStr, StrictStr]


class Basis(_Out):
    """이 턴이 읽은 것(6-8). 피치마다 · Take마다 버전과 결과 판 · 규칙 판."""

    pitches: list[dict[str, Any]] = Field(default_factory=list)
    conversation_goal_version: NonNegInt | None = None
    corpus_version: StrictStr | None = None
    sources: list[StrictStr] = Field(default_factory=list)


class FinalData(_Out):
    status: FinalStatus
    message: Message | None
    actions: list[Action]
    state: dict[str, Any] | None
    scope_pitch_id: StrictStr | None
    plan: list[PlanStep]
    trace: list[TraceItem]
    basis: Basis
    evidence: list[Evidence]
    history_summary: StrictStr | None = None
    budget_used: BudgetOut
    usage: Usage
    assessments: list[Assessment]
    goal_result: GoalResult | None = None
    meta: Meta
    diagnostics: Diagnostics | None = None

    @model_validator(mode="after")
    def _status_combo(self) -> FinalData:
        s = self.status
        if s == "NEEDS_ACTION":
            if len(self.actions) != 1 or self.actions[0].type == "START_PRACTICE":
                raise ValueError("NEEDS_ACTION 은 행동 정확히 1개(START_PRACTICE 아님)")
            if self.state is None:
                raise ValueError("NEEDS_ACTION 은 state 필수")
            if self.actions[0].type == "REASSESS" and self.assessments:
                ids = {a.assessment_id for a in self.assessments}
                linked = {
                    c.assessment_id
                    for c in (self.message.claims if self.message else [])
                    if c.kind == "ASSESSMENT"
                }
                if not ids <= linked:
                    raise ValueError(
                        "REASSESS 를 담은 NEEDS_ACTION 의 판단마다 판단 문장이 있어야 한다"
                    )
        else:
            if self.state is not None:
                raise ValueError("NEEDS_ACTION 이 아니면 state 는 null")
            if self.message is None:
                raise ValueError("DONE · PARTIAL · REFUSED 는 message 필수")
            if s == "DONE":
                if len(self.actions) > 1 or any(a.type != "START_PRACTICE" for a in self.actions):
                    raise ValueError("DONE 의 actions 는 START_PRACTICE 0~1개")
            elif self.actions:
                raise ValueError("PARTIAL · REFUSED 의 actions 는 빈 목록")
        return self


class ErrorBody(_Out):
    error: dict[str, str]


# ── 5-4 스트림 줄 ───────────────────────────────────────────────────────────


class StreamLine(_Out):
    event: Literal["plan", "tool_done", "final", "error"]
    run_id: StrictStr
    seq: PosInt
    data: dict[str, Any]
