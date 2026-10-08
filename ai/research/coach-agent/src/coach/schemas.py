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
    GazeLevel,
    Instruction,
    Issue,
    MemoryLabel,
    MissionStatus,
    Mode,
    Outcome,
    PaceLevel,
    Schedule,
    SegmentHint,
    StrategyChange,
    StrengthKind,
    TypeStatus,
    VolumeLevel,
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
    target_ms: int = Field(gt=0)
    #: 이 장 대본 글자 수 (공백 제외). 진행도 = 이 장에서 말한 글자 수 / script_chars
    script_chars: int = Field(default=0, ge=0)
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
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ"]
    value: float


class Mission(_In):
    """직전 리뷰의 next_missions 그대로."""

    mission_id: str
    type: FeedbackType
    slide_number: int | None = None
    priority: int = 1
    description: str | None = None
    target: MissionTarget | None = None


class RecurringIssue(_In):
    type: FeedbackType
    slide_number: int | None = None


class Memory(_In):
    """이전 Take 기억. 직전 리뷰가 '아직 남은 문제'로 꼽은 것."""

    recurring_issues: list[RecurringIssue] = Field(default_factory=list)


class TimingInput(_In):
    slide_number: int | None = None
    slide_elapsed_ms: int | None = Field(default=None, ge=0)


class GazeInput(_In):
    """FE 시선 모듈의 최근 창 요약. 라벨 이름이 늘어도 모양은 그대로다."""

    window_ms: int = 10_000
    ratios: dict[str, float] = Field(default_factory=dict)
    current_label: str | None = None
    #: current_label 이 이어진 시간
    current_label_ms: int | None = Field(default=None, ge=0)


class VoiceInput(_In):
    #: 캘리브레이션(평소 목소리) 대비 dB. 음수가 작은 소리. 말하지 않는 중이면 null
    relative_db: float | None = None
    #: 지금 몇 ms 째 조용한가
    silence_ms: int = Field(default=0, ge=0)
    #: 오디오가 실제로 흐르는가. False 면 소리 판단을 전부 끈다
    audio_live: bool = True


class Word(_In):
    w: str
    start_ms: int
    end_ms: int
    #: 확정 단어만 누적(진행도 · 군더더기)에 쓴다. 중간 결과는 CPM 에만 쓴다
    final: bool = True
    #: BE 의 fillers.py 목록에 있는 말인가. 군더더기 목록의 단일 소스가 BE 라서 BE 가 표시한다
    filler: bool = False


class SpeechInput(_In):
    #: BE 의 stt_status 값. "ok" 가 아니면 STT 에 기대는 판단을 끈다
    stt_status: str = "ok"
    #: 최근 window_ms(15초) 의 단어. 확정과 중간 결과가 겹치지 않게 보낸다
    words: list[Word] = Field(default_factory=list)
    #: Deepgram 이 마지막으로 알려준 문장 끝 시각 (speech_final / UtteranceEnd)
    utterance_end_ms: int | None = None


class Current(_In):
    """지금 1초의 측정값. 영역이 빠지면 그 영역 판단만 건너뛴다."""

    timing: TimingInput | None = None
    gaze: GazeInput | None = None
    voice: VoiceInput | None = None
    speech: SpeechInput | None = None


class CoachRequest(_In):
    schema_version: str = SCHEMA_VERSION
    take_id: str
    #: Take 시작 기준 경과 ms. 모든 시각이 이 시간축이다
    t_ms: int = Field(ge=0)
    mode: Mode = Mode.PRACTICE
    plan: Plan = Field(default_factory=Plan)
    missions: list[Mission] = Field(default_factory=list)
    memory: Memory = Field(default_factory=Memory)
    current: Current = Field(default_factory=Current)
    #: 지난 응답의 coach_state 그대로. 첫 요청이면 null. BE 는 내용을 몰라도 된다
    coach_state: dict[str, Any] | None = None


# ══════════════════════════════════════════════════════════════════════════
# 코칭 계획 — v1 은 기본값, v1.1 에서 LLM 이 Take 시작 전에 만든다
# ══════════════════════════════════════════════════════════════════════════


class FocusItem(_In):
    type: FeedbackType
    slide_number: int | None = None
    #: config.policy.plan_weight_min ~ max 로 잘린다
    weight: float = 1.0
    why: str = ""


class RelaxItem(_In):
    type: FeedbackType
    slide_number: int | None = None
    why: str = ""


class CoachingPlan(_In):
    source: Literal["DEFAULT", "LLM"] = "DEFAULT"
    focus: list[FocusItem] = Field(default_factory=list)
    relax: list[RelaxItem] = Field(default_factory=list)
    max_interventions: int | None = Field(default=None, ge=0)


# ══════════════════════════════════════════════════════════════════════════
# 응답
# ══════════════════════════════════════════════════════════════════════════


class Feedback(_Out):
    type: FeedbackType
    instruction: Instruction
    message: str
    priority: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    #: start_ms · end_ms · slide_number + 모듈이 준 값
    evidence: dict[str, Any]


class CandidateOut(_Out):
    candidate_id: str
    issue: Issue
    type: FeedbackType
    instruction: Instruction
    priority: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    status: CandidateStatus
    reasons: list[str] = Field(default_factory=list)


class Indicators(_Out):
    """읽지 않아도 되는 상태 표시. 지시(feedback)는 1개지만 이건 여러 개를 함께 띄워도 된다."""

    schedule: Schedule = Schedule.UNKNOWN
    required_ratio: float | None = None
    pace: PaceLevel = PaceLevel.UNKNOWN
    cpm: float | None = None
    gaze: GazeLevel = GazeLevel.UNKNOWN
    volume: VolumeLevel = VolumeLevel.UNKNOWN


# ── 이벤트 — BE 가 그대로 쌓아 두는 기록. 리뷰 에이전트 근거의 원천 ──────────


class _Event(_Out):
    event_id: str
    t_ms: int


class InterventionEvent(_Event):
    kind: Literal["INTERVENTION"] = "INTERVENTION"
    candidate_id: str
    issue: Issue
    type: FeedbackType
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
    issue: Issue
    type: FeedbackType
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
    issue: Issue
    type: FeedbackType
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
    keywords_required: list[str] = Field(default_factory=list)
    keywords_found: list[str] = Field(default_factory=list)


class SuppressedEvent(_Event):
    """걸렸지만 말하지 않은 기록. 같은 문제는 suppress_log_gap_ms 에 한 번만 남는다."""

    kind: Literal["SUPPRESSED"] = "SUPPRESSED"
    candidate_id: str
    issue: Issue
    type: FeedbackType
    instruction: Instruction
    status: CandidateStatus
    priority: int
    reasons: list[str]


class StrategyEvent(_Event):
    """효과가 없어 방법을 바꿨거나(ESCALATED) 그만뒀다(GAVE_UP)."""

    kind: Literal["STRATEGY"] = "STRATEGY"
    issue: Issue
    type: FeedbackType
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
    indicators: Indicators = Field(default_factory=Indicators)
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
    """장별 표 한 줄. 리뷰의 구간 표(CAMERA/BOTTOM · Pace · Filler · Keyword)와 같은 축."""

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
    relative_db: float | None
    filler_count: int
    filler_per_min: float | None
    long_silence_ms: int
    keyword_coverage: float | None
    keywords_missing: list[str]
    gaze_coverage: float | None
    speech_coverage: float | None
    audio_coverage: float | None
    #: 이 장에서 문제로 본 영역
    issue_types: list[FeedbackType]


class IssueReview(_Out):
    """문제 하나 (영역 × 장). rank 1 이 다음에 먼저 고칠 것."""

    rank: int
    type: FeedbackType
    #: null 이면 Take 전체에 걸친 문제
    slide_number: int | None
    issues: list[Issue]
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

    type: FeedbackType
    status: TypeStatus
    burden_s: float
    #: 이전 Take 기억에 이 영역이 있었으면 그 결과
    memory: MemoryLabel | None = None
    evidence: dict[str, Any]


class StrengthReview(_Out):
    kind: StrengthKind
    type: FeedbackType
    slide_number: int | None = None
    evidence: dict[str, Any]


class MissionReview(_Out):
    """이전 Mission 판정. 리뷰의 previous_mission_result / MissionResult 로 그대로 옮긴다."""

    mission_id: str
    type: FeedbackType
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

    type: FeedbackType
    slide_number: int | None
    label: MemoryLabel
    burden_s: float


class NextMissionTarget(_Out):
    metric: str
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ"]
    value: float


class NextMission(_Out):
    """다음 Take 미션 후보. target 은 Mission.target 그대로라 다음 Take 에서 기계로 판정된다."""

    priority: int
    type: FeedbackType
    slide_number: int | None
    target: NextMissionTarget
    #: 이번 Take 에서 잰 값 (목표는 여기서 한 번에 도달할 만큼만 낮춘다)
    observed: float | None
    reason_codes: list[str]
    evidence: dict[str, Any]


class TypeSummary(_Out):
    type: FeedbackType
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
    type: FeedbackType
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
    type: FeedbackType
    issue: Issue
    slide_number: int | None
    change: StrategyChange
    from_instruction: Instruction
    to_instruction: Instruction | None


class SegmentReview(_Out):
    """리뷰의 SegmentReview 와 같은 축(slide_number · start_ms · end_ms · type · evidence).

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
    type: FeedbackType
    issue: Issue
    issues: list[Issue]
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
