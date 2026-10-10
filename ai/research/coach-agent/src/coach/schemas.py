"""BE ↔ 코치 계약 (pydantic).

이 파일이 요청 · 응답 · 이벤트 · 리뷰 근거 모양의 단일 진실 원천입니다.

입력 모델은 모르는 필드를 무시합니다(extra="ignore") — BE 가 필드를 먼저 추가해도 깨지지 않게.
필드 이름은 snake_case 입니다 (BE DTO · 리뷰 DTO 와 같게).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .version import SCHEMA_VERSION
from .vocab import (
    Action,
    CandidateStatus,
    FeedbackType,
    Instruction,
    Issue,
    MemoryLabel,
    MissionStatus,
    Mode,
    Outcome,
    SegmentHint,
    StrategyChange,
    StrengthKind,
    TypeStatus,
)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ══════════════════════════════════════════════════════════════════════════
# 요청 — POST /coach/evaluate (1초마다)
# ══════════════════════════════════════════════════════════════════════════


class SlidePlan(_In):
    """대본 분석이 주는 장별 계획."""

    slide_number: int
    #: 0 이면 그 장의 장별 문제(SLIDE_OVER)를 내지 않는다
    target_ms: int = Field(ge=0)
    #: 이 장 대본 글자 수 (공백 제외). 진행도 = 이 장에서 말한 글자 수 / script_chars
    script_chars: int = Field(default=0, ge=0)
    #: 코칭 계획(LLM) 입력에만 쓴다. 발표 중 코치는 보지 않는다
    required_keywords: list[str] = Field(default_factory=list)


class Plan(_In):
    """발표 계획. Take 동안 바뀌지 않는다."""

    target_ms: int | None = Field(default=None, gt=0)
    #: 허용 범위. 예상 종료가 min_ms 보다 이르면 '너무 빨리 끝남', max_ms 를 넘으면 시간 초과
    min_ms: int | None = None
    max_ms: int | None = None
    slides: list[SlidePlan] = Field(default_factory=list)


class MissionTarget(_In):
    #: 지표 이름. 응답 evidence · 리뷰 evidence 와 같은 이름을 쓴다 (INTERFACE.md 의 공통 지표 이름)
    metric: str
    operator: Literal["LT", "LTE", "GT", "GTE"]
    value: float


class Mission(_In):
    """직전 리뷰의 next_missions 그대로."""

    mission_id: str
    area: FeedbackType
    slide_number: int | None = None
    priority: int = 1
    description: str | None = None
    target: MissionTarget | None = None


class RecurringIssue(_In):
    area: FeedbackType
    slide_number: int | None = None


class Memory(_In):
    """이전 Take 기억. 직전 리뷰가 '아직 남은 문제'로 꼽은 것."""

    recurring_issues: list[RecurringIssue] = Field(default_factory=list)


class GazeRecordIn(_In):
    """FE 가 보내는 시선 1초 기록(#153)."""

    t_ms: int = Field(ge=0)
    duration_ms: int = Field(default=1000, gt=0)
    state: str
    direction: str | None = None
    confidence: float | None = None
    reliability: float | None = None
    issues: list[str] = Field(default_factory=list)
    frames: int | None = None


class VoiceRecordIn(_In):
    """FE 가 보내는 음량 1초 기록(#155). 시선 판정도 같은 1초들의 voiced_ms 를 쓴다."""

    t_ms: int = Field(ge=0)
    duration_ms: int = Field(default=1000, gt=0)
    level_db: float | None = None
    voiced_ms: int = 0
    silence_ms: int = 0
    audio_live: bool = True


class WordIn(_In):
    """STT 확정 단어."""

    word: str
    start_ms: int
    end_ms: int


class SlideNow(_In):
    """지금 장과 그 장이 시작된 시각."""

    number: int
    started_ms: int


class CoachInputs(_In):
    """판정 모듈에 넘길 원자료. 창 길이는 judges.py 의 GAZE_VOICE_WINDOW_MS · WORDS_WINDOW_MS."""

    gaze_records: list[GazeRecordIn] = Field(default_factory=list)
    voice_records: list[VoiceRecordIn] = Field(default_factory=list)
    words: list[WordIn] = Field(default_factory=list)
    utterance_ends: list[int] = Field(default_factory=list)
    stt_status: str = "ok"
    slide: SlideNow | None = None


class Calibration(_In):
    """Take 밖에서 잡아 둔 값. base_level_db 가 null 이면 코치가 Take 첫 발화로 잡는다."""

    base_level_db: float | None = None


class CoachRequest(_In):
    take_id: str
    #: Take 시작 기준 경과 ms. 모든 시각이 이 시간축이다
    t_ms: int = Field(ge=0)
    mode: Mode = Mode.COACHING
    plan: Plan = Field(default_factory=Plan)
    missions: list[Mission] = Field(default_factory=list)
    #: 직전 리뷰의 반복 문제
    recurring_issues: list[RecurringIssue] = Field(default_factory=list)
    #: 코칭 계획. null 이면 계획 없음. why · source 는 와도 쓰지 않는다
    coaching_plan: CoachingPlan | None = None
    #: 사용자 평가 기준의 대본 사용 설정. 개입 규칙에서 쓴다 (아직 쓰지 않는다)
    script_used: bool | None = None
    #: 판정 모듈에 넘길 원자료
    inputs: CoachInputs = Field(default_factory=CoachInputs)
    calibration: Calibration = Field(default_factory=Calibration)
    #: 지난 응답의 coach_state 그대로. 첫 요청이면 null. BE 는 내용을 몰라도 된다
    coach_state: dict[str, Any] | None = None


# ══════════════════════════════════════════════════════════════════════════
# 코칭 계획 — v1.1 까지는 기본값, v1.2 에서 LLM 이 Take 시작 전에 만든다
# ══════════════════════════════════════════════════════════════════════════


class FocusItem(_In):
    area: FeedbackType
    slide_number: int | None = None
    #: config.policy.plan_weight_min ~ max 로 잘린다
    weight: float = 1.0
    why: str = ""


class RelaxItem(_In):
    area: FeedbackType
    slide_number: int | None = None
    why: str = ""


class CoachingPlan(_In):
    source: Literal["DEFAULT", "LLM"] = "DEFAULT"
    focus: list[FocusItem] = Field(default_factory=list)
    relax: list[RelaxItem] = Field(default_factory=list)
    max_interventions: int | None = Field(default=None, ge=0)


class SlideScript(_In):
    """장 하나의 대본. 계획을 세울 때만 쓴다 (1초 판단에는 글자 수만 쓴다)."""

    slide_number: int
    script: str = ""


class PlanRequest(_In):
    """Take 시작 전 코칭 계획 요청. 이번 Take 의 계획 · 장별 대본 · 미션 · 기억 · 직전 리뷰 근거."""

    schema_version: str = SCHEMA_VERSION
    take_id: str
    mode: Mode = Mode.COACHING
    plan: Plan = Field(default_factory=Plan)
    scripts: list[SlideScript] = Field(default_factory=list)
    missions: list[Mission] = Field(default_factory=list)
    memory: Memory = Field(default_factory=Memory)
    #: 직전 Take 의 리뷰 근거(build_review_evidence 결과) 그대로. 없으면 null
    previous_review: dict[str, Any] | None = None


class PlanFocusDraft(BaseModel):
    """LLM 출력 — 집중할 영역. 출력 스키마는 프롬프트와 함께 계획 해시에 들어간다."""

    type: FeedbackType
    slide_number: int | None = Field(description="장 번호. Take 전체면 null")
    weight: float = Field(description="우선순위 가중치 1.2 ~ 2.0. 클수록 먼저 챙긴다")
    why: str = Field(description="대본 · 미션 · 이전 리뷰에서 찾은 짧은 근거 한 문장")


class PlanRelaxDraft(BaseModel):
    """LLM 출력 — 이 장에서는 지적하지 않아도 되는 영역."""

    type: FeedbackType
    slide_number: int = Field(description="장 번호. 장 단위로만 봐줄 수 있다")
    why: str = Field(description="대본에서 찾은 짧은 근거 한 문장")


class PlanDraft(BaseModel):
    """LLM 이 내는 코칭 계획 초안. 코치가 검증하고 값을 잘라 CoachingPlan 으로 만든다."""

    focus: list[PlanFocusDraft]
    relax: list[PlanRelaxDraft]
    max_interventions: int | None = Field(
        description="이번 Take 에서 말할 최대 횟수. 제한할 이유가 없으면 null"
    )


class PlanResponse(_Out):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    #: 프롬프트 · 출력 스키마 · 모델로 만든 해시. 같은 해시 · 같은 입력이면 같은 질문이다
    planner_hash: str
    take_id: str
    plan: CoachingPlan
    #: 기본 계획으로 돌아간 이유 (EXAM_MODE · NO_LLM · LLM_ERROR). LLM 계획을 썼으면 null
    fallback_reason: str | None = None
    #: 검증에서 뺀 항목과 이유 (예: "relax TIME 3: 봐줄 수 없는 영역")
    dropped: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 응답
# ══════════════════════════════════════════════════════════════════════════


class Feedback(_Out):
    area: FeedbackType
    instruction: Instruction
    message: str
    priority: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    #: start_ms · end_ms · slide_number + 모듈이 준 값
    evidence: dict[str, Any]


class CandidateOut(_Out):
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    priority: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    status: CandidateStatus
    reasons: list[str] = Field(default_factory=list)


# ── 이벤트 — BE 가 그대로 쌓아 두는 기록. 리뷰 에이전트 근거의 원천 ──────────


class _Event(_Out):
    event_id: str
    t_ms: int


class InterventionEvent(_Event):
    kind: Literal["INTERVENTION"] = "INTERVENTION"
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    variant: str
    message: str
    priority: int
    confidence: float
    reason_codes: list[str]
    slide_number: int | None = None
    evidence: dict[str, Any]


class OutcomeEvent(_Event):
    """개입 몇 초 뒤 실제로 행동이 바뀌었는가."""

    kind: Literal["OUTCOME"] = "OUTCOME"
    intervention_id: str
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    slide_number: int | None = None
    outcome: Outcome
    metric: str | None = None
    before: float | None = None
    after: float | None = None


class EpisodeEvent(_Event):
    """문제 구간 하나가 끝났다. 개입했든 안 했든 남긴다 — 리뷰의 '문제 구간'이 된다."""

    kind: Literal["EPISODE"] = "EPISODE"
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    start_ms: int
    end_ms: int
    peak_severity: float
    intervention_ids: list[str] = Field(default_factory=list)
    #: 이 구간에서 말하지 못한 이유들 (EXAM_MODE, MIN_GAP …)
    suppressed_reasons: list[str] = Field(default_factory=list)
    closed_by: Literal["RESOLVED", "TAKE_END"]
    #: 구간 동안 가장 나빴을 때의 지표
    peak_evidence: dict[str, Any] = Field(default_factory=dict)
    #: 센서를 믿을 수 있던 / 없던 시간. 대부분 믿을 수 없었다면 리뷰는 이 구간을 문제로 말하지
    #: 않는다
    reliable_ms: int = 0
    unreliable_ms: int = 0
    #: 구간 평균 심각도. 부담(심각도 × 초)은 이걸로 잰다 — 최고값은 잡음에 크게 흔들린다
    mean_severity: float = 0.0


class SlideEvent(_Event):
    """한 장에 머문 동안의 누적. 장을 떠날 때(또는 Take 종료 때) 하나씩. 다시 돌아오면 또 하나.

    평균은 *_weighted / *_ms 로 낸다 (예: CPM = cpm_weighted / cpm_ms).
    """

    kind: Literal["SLIDE"] = "SLIDE"
    #: null 이면 장 정보가 없는 발표 (Take 전체 누적에만 들어간다)
    slide_number: int | None
    start_ms: int
    end_ms: int
    target_ms: int | None = None
    script_chars: int | None = None
    total_ms: int
    gaze_valid_ms: int
    gaze_script_ms: float
    gaze_unusable_ms: int
    speech_ok_ms: int
    cpm_ms: int
    cpm_weighted: float
    filler_count: int
    audio_live_ms: int
    speaking_ms: int
    db_ms: int
    db_weighted: float
    long_silence_ms: int
    #: 이 장에서 지금까지 말한 글자 수 (다시 돌아온 장이면 누적)
    chars_total: int


class SuppressedEvent(_Event):
    """걸렸지만 말하지 않은 기록. 같은 문제는 suppress_log_gap_ms 에 한 번만 남는다."""

    kind: Literal["SUPPRESSED"] = "SUPPRESSED"
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    status: CandidateStatus
    priority: int
    reasons: list[str]


class StrategyEvent(_Event):
    """효과가 없어 방법을 바꿨거나(ESCALATED) 그만뒀다(GAVE_UP)."""

    kind: Literal["STRATEGY"] = "STRATEGY"
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    change: StrategyChange
    from_instruction: Instruction
    from_variant: str
    to_instruction: Instruction | None = None
    to_variant: str | None = None
    failures: int
    intervention_id: str


CoachEvent = Annotated[
    InterventionEvent | OutcomeEvent | EpisodeEvent | SuppressedEvent | StrategyEvent | SlideEvent,
    Field(discriminator="kind"),
]


class CoachResponse(_Out):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    config_hash: str
    take_id: str
    t_ms: int
    action: Action
    #: 이번 판단의 대상이 된 문제. "문제코드-시작시각" 이라 문제가 이어지는 동안 같다
    candidate_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    #: INTERVENE 일 때만
    feedback: Feedback | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)
    #: 영역(GAZE · SPEED · VOLUME · PAUSE · FILLER · TIME) → 판정 모듈이 준 상태. 읽지 않아도 되는
    #: 상태 표시라 여러 개를 함께 띄워도 된다
    indicators: dict[str, str] = Field(default_factory=dict)
    events: list[CoachEvent] = Field(default_factory=list)
    #: 다음 요청에 그대로 붙인다
    coach_state: dict[str, Any]


# ══════════════════════════════════════════════════════════════════════════
# Take 종료 — 열린 구간을 닫는다
# ══════════════════════════════════════════════════════════════════════════


class FinalizeRequest(_In):
    schema_version: str = SCHEMA_VERSION
    take_id: str
    t_ms: int = Field(ge=0)
    coach_state: dict[str, Any] | None = None


class FinalizeResponse(_Out):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    take_id: str
    events: list[CoachEvent] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 리뷰 에이전트로 넘기는 근거 — 이벤트 수백 개 대신 요약된 구조
#
# 숫자는 전부 코드가 이벤트에서 계산한 것이다. 리뷰 에이전트(LLM)는 이 숫자를 인용만 하고
# 새로 만들지 않는다 — 리뷰가 근거 없는 수치를 만들어 내지 않게 하기 위한 구조.
# 문장은 만들지 않는다. 꼬리표 · 상태 · 순위 · 목표값까지만 주고 문장은 리뷰가 쓴다.
# ══════════════════════════════════════════════════════════════════════════


class ReviewEvidenceRequest(_In):
    schema_version: str = SCHEMA_VERSION
    take_id: str
    events: list[CoachEvent] = Field(default_factory=list)
    #: 이 Take 의 요청에 들어갔던 것과 같은 계획 · 미션 · 기억. 미션 판정과 이전 Take 비교에 쓴다
    plan: Plan | None = None
    missions: list[Mission] = Field(default_factory=list)
    memory: Memory = Field(default_factory=Memory)


class CoachingSummary(_Out):
    #: 화면에 띄운 전체 (교정 + 유지 격려)
    interventions: int
    #: 그중 유지 격려(CONTINUE)
    praises: int
    effective: int
    ineffective: int
    not_measured: int
    #: 효과를 잰 개입 중 효과 있던 비율. 잰 개입이 없으면 null
    effective_rate: float | None
    gave_up: int
    episodes: int
    #: 개입하지 못한(또는 안 한) 문제 구간 수 (믿을 수 없던 구간은 빼고)
    episodes_unaddressed: int
    #: 참은 기록 수 (suppress_log_gap_ms 간격으로 남긴 것이라 대략값)
    suppressed_by_reason: dict[str, int]


class DataQuality(_Out):
    """센서가 얼마나 살아 있었나. 모자란 영역은 리뷰가 '판단 불가'라고 말해야 한다."""

    duration_ms: int
    #: 시선 판단을 믿을 수 있던 시간 비율
    gaze_coverage: float | None
    #: STT 를 믿을 수 있던 시간 비율
    speech_coverage: float | None
    #: 오디오가 살아 있던 시간 비율
    audio_coverage: float | None
    #: 센서를 믿을 수 없어 문제에서 뺀 구간 수
    unreliable_segments: int
    #: 영역별 판단 가능 여부
    evaluable: dict[str, bool]


class SlideReview(_Out):
    """장별 표 한 줄. 리뷰의 구간 표(CAMERA/BOTTOM · Pace · Filler)와 같은 축."""

    slide_number: int
    visits: int
    start_ms: int
    duration_ms: int
    target_ms: int | None
    #: 목표보다 더 쓴 시간 (음수면 덜 씀)
    over_ms: int | None
    #: 보인 시간 중 대본 응시 비율
    script_ratio: float | None
    cpm: float | None
    voice_diff_db: float | None
    filler_count: int
    filler_per_min: float | None
    long_silence_ms: int
    gaze_coverage: float | None
    speech_coverage: float | None
    audio_coverage: float | None
    #: 이 장에서 문제로 본 영역
    areas: list[FeedbackType]


class IssueReview(_Out):
    """문제 하나 (영역 × 장). rank 1 이 다음에 먼저 고칠 것."""

    rank: int
    area: FeedbackType
    #: null 이면 Take 전체에 걸친 문제
    slide_number: int | None
    issue_types: list[Issue]
    #: 심각도 × 초. 얼마나 크게 · 오래 문제였나
    burden_s: float
    #: 순위 점수 = 부담 × 반복 · 포기 · 미션 실패 가중치
    score: float
    duration_ms: int
    segments: int
    peak_severity: float
    interventions: int
    effective: int
    ineffective: int
    gave_up: bool
    memory: MemoryLabel
    mission_failed: bool
    evidence: dict[str, Any]


class TypeStatusReview(_Out):
    """영역별 상태. 리뷰 dimension(audience_gaze …)의 IMPROVED / PRIORITY / STABLE / STRENGTH."""

    area: FeedbackType
    status: TypeStatus
    burden_s: float
    #: 이전 Take 기억에 이 영역이 있었으면 그 결과
    memory: MemoryLabel | None = None
    evidence: dict[str, Any]


class StrengthReview(_Out):
    kind: StrengthKind
    area: FeedbackType
    slide_number: int | None = None
    evidence: dict[str, Any]


class MissionReview(_Out):
    """이전 Mission 판정. 리뷰의 previous_mission_result / MissionResult 로 그대로 옮긴다."""

    mission_id: str
    area: FeedbackType
    slide_number: int | None
    status: MissionStatus
    achieved: bool
    metric: str
    operator: str | None
    target: float | None
    #: 이번 Take 에서 잰 값
    observed: float | None
    #: 그 값을 잰 데이터가 덮은 시간 비율
    coverage: float | None
    #: NOT_EVALUABLE 인 이유 (NOT_REACHED · LOW_DATA_COVERAGE · UNSUPPORTED_METRIC · NO_TARGET ·
    #: NO_DATA)
    reason: str | None = None


class MemoryReview(_Out):
    """이전 Take 기억 한 줄과 이번 Take 비교."""

    area: FeedbackType
    slide_number: int | None
    label: MemoryLabel
    burden_s: float


class NextMissionTarget(_Out):
    metric: str
    operator: Literal["LT", "LTE", "GT", "GTE"]
    value: float


class NextMission(_Out):
    """다음 Take 미션 후보. target 은 Mission.target 그대로라 다음 Take 에서 기계로 판정된다."""

    priority: int
    area: FeedbackType
    slide_number: int | None
    target: NextMissionTarget
    #: 이번 Take 에서 잰 값 (목표는 여기서 한 번에 도달할 만큼만 낮춘다)
    observed: float | None
    reason_codes: list[str]
    evidence: dict[str, Any]


class TypeSummary(_Out):
    area: FeedbackType
    interventions: int
    effective: int
    ineffective: int
    not_measured: int
    episodes: int
    episodes_unaddressed: int


class InterventionReview(_Out):
    intervention_id: str
    t_ms: int
    slide_number: int | None
    area: FeedbackType
    instruction: Instruction
    message: str
    reason_codes: list[str]
    evidence: dict[str, Any]
    outcome: Outcome
    outcome_metric: str | None = None
    before: float | None = None
    after: float | None = None


class StrategyReview(_Out):
    t_ms: int
    area: FeedbackType
    issue_type: Issue
    slide_number: int | None
    change: StrategyChange
    from_instruction: Instruction
    to_instruction: Instruction | None


class SegmentReview(_Out):
    """리뷰의 SegmentReview 와 같은 축(slide_number · start_ms · end_ms · area · evidence).

    start_ms / end_ms 는 코치가 실제로 문제를 잡은 구간, onset_ms / offset_ms 는 평가기 창의
    지연을 되돌린 추정 구간입니다. 리뷰가 "몇 분 몇 초부터"를 말할 때는 onset / offset 을 쓰세요.
    """

    candidate_id: str
    candidate_ids: list[str]
    slide_number: int | None
    start_ms: int
    end_ms: int
    onset_ms: int
    offset_ms: int
    area: FeedbackType
    issue_type: Issue
    issue_types: list[Issue]
    peak_severity: float
    mean_severity: float
    #: 센서를 믿을 수 있던 시간 비율
    reliability: float
    hint: SegmentHint
    intervention_ids: list[str]
    suppressed_reasons: list[str]
    evidence: dict[str, Any]


class CoachReviewEvidence(_Out):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    config_hash: str
    take_id: str
    # ── 리뷰가 먼저 볼 것 ──────────────────────────────────────────────
    summary: CoachingSummary
    data_quality: DataQuality
    #: 영역별 상태 → dimension 상태
    type_status: list[TypeStatusReview]
    #: 순위 매긴 문제 → improvements / remaining_issues / new_issues
    issues: list[IssueReview]
    #: 다음 Mission 후보 (목표값 포함) → next_missions
    next_missions: list[NextMission]
    #: 이전 Mission 판정 → mission_results / previous_mission_result
    mission_results: list[MissionReview]
    #: 이전 Take 기억 비교 → improved / remaining_issues
    memory_check: list[MemoryReview]
    #: 강점 → strengths
    strengths: list[StrengthReview]
    # ── 세부 근거 ────────────────────────────────────────────────────
    slides: list[SlideReview]
    segments: list[SegmentReview]
    interventions: list[InterventionReview]
    strategy_changes: list[StrategyReview]
    by_type: list[TypeSummary]


# ══════════════════════════════════════════════════════════════════════════
# 공통 1초 판정 결과 (#158 정의의 복사본)
#
# 판정 모듈(시선 · 속도 · 음량 · 군더더기 · 시간)이 같은 모양으로 낸다.
# 코치는 외부 모듈의 결과도 이 모양으로 읽는다 — 그래서 모르는 필드는 무시한다.
# ══════════════════════════════════════════════════════════════════════════


class JudgmentIssue(_In):
    """판정 결과 안의 문제 하나."""

    issue_type: Issue
    area: FeedbackType
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    persistence_sec: float = Field(ge=0.0)
    threshold: float
    bad: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    actionable: bool = True


class TallyItem(_In):
    """집계 조각. 구간마다 값을 남겨 두면 소비자가 원하는 구간으로 합친다."""

    t_ms: int
    values: dict[str, float]


class JudgmentResult(_In):
    """한 판정 모듈의 1초 판정 결과."""

    #: gaze · pace · volume · filler · timing
    evaluator: str
    area: FeedbackType
    t_ms: int
    #: 여기까지 집계(tally)에 셌다. 다음 호출의 since_ms 로 쓴다
    counted_until_ms: int
    words_counted_until_ms: int | None = None
    criteria_version: str
    measurable: bool
    state: str
    metrics: dict[str, float | None] = Field(default_factory=dict)
    tally: list[TallyItem] = Field(default_factory=list)
    issues: list[JudgmentIssue] = Field(default_factory=list)
    #: filler 모듈만 쓰는 확장 필드
    words: list[dict[str, Any]] | None = None


class IssueCriteria(_In):
    """문제 하나의 판정 기준. 소비자가 같은 기준으로 다시 셀 수 있게 공개한다."""

    metric: str
    direction: Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]
    threshold: float
    bad: float
    onset_lag_ms: int = 0
    offset_lag_ms: int = 0
