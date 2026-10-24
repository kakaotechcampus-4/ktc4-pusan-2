import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from pitch_coach_backend.core.validation import NulFreeStr, reject_unstorable_json

# takes 의 CHECK 와 같은 값이다. FE types/api.ts 의 PracticeMode · ScriptMode 와도 같다
TakeMode = Literal["COACHING", "EXAM"]
ScriptMode = Literal["HIGHLIGHT", "KEYWORD", "OFF"]


# 목표 시간은 받지 않는다. 발표 제한 시간을 그대로 박는다 (FE 가 보내도 무시한다)
class TakeInitRequestDTO(BaseModel):
    mode: TakeMode
    script_mode: ScriptMode
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID
    # 응답을 잃고 같은 값으로 다시 보내면 새로 만들지 않고 처음 만든 Take 를 돌려준다.
    # 본문이 처음과 다르거나 그 Take 가 이미 끝났으면 409
    client_session_id: uuid.UUID | None = None

# 보낸 필드만 반영한다 (service 가 exclude_none 으로 읽는다).
# 시각은 시간대가 있어야 한다. 없으면 DB 의 시각과 앞뒤를 비교할 수 없다
class TakeUpdateRequestDTO(BaseModel):
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    # takes.event_logs(JSONB) 에 그대로 들어간다
    event_logs: (
        Annotated[list[dict[str, Any]], AfterValidator(reject_unstorable_json)] | None
    ) = None

# Take 기록 · Take 비교 화면의 한 줄. FE types/takeHistory.ts 와 같은 모양이다.
# 못 잰 값은 0 이 아니라 null 이다
class TakeHistoryRowDTO(BaseModel):
    take_id: uuid.UUID
    pitch_id: uuid.UUID
    pitch_title: str
    take_number: int
    started_at: datetime
    status: str
    script_mode: str
    # 끝나지 않았으면 null
    duration_ms: int | None
    goal_ms: int
    # 시선이 청중 · 대본에 머문 비율 (0~1)
    audience_ratio: float | None
    script_ratio: float | None
    cpm: float | None
    filler_count: int | None
    # Take 를 시작할 때 실은 미션 중 리뷰가 달성으로 판정한 수 / 실은 수
    missions_achieved: int
    missions_total: int
    # 장치 점검 때의 시선 엔진 버전. 다르면 FE 가 시선 값을 나란히 비교하지 않는다
    gaze_engine: str | None

class TakeHistoryResponseDTO(BaseModel):
    takes: list[TakeHistoryRowDTO]

class TakeSummaryDTO(BaseModel):
    take_id: uuid.UUID
    take_version: int
    take_elapsed: int | None = None
    take_time: int
    script_mode: str
    score: int | None = None
    delta: int | None = None
    created_at: datetime | None = None

# calibrations.result(JSONB) 에 그대로 들어가서 NUL 문자열과 NaN · Infinity 를 받지 않는다
class CalibrationResultDTO(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    engine_version: NulFreeStr | None = None
    status: NulFreeStr | None = None
    separability: float | None = None
    loo_accuracy: float | None = None
    warnings: list[NulFreeStr] | None = None

class CalibrationDTO(BaseModel):
    base_level_db: float | None = None
    result: CalibrationResultDTO | None = None
    face_detected: bool = False
    mic_detected: bool = False
    # DB 는 Numeric(5, 2) — 999.99 까지. lt=1000 이면 999.995 가 반올림돼 1000.00 이 되어 넘친다
    base_volume: float = Field(default=0.0, ge=0, le=999.99)
    # DB 는 Float. bool 을 넘기면 psycopg 가 타입이 안 맞는다고 거절해 저장이 항상 500 이었다
    gaze_confidence: float = 0.0

class MissionInputDTO(BaseModel):
    mission_id: uuid.UUID
    area: str
    slide_number: int | None = None
    target: TargetInfoDTO

class PreviousTakeDTO(BaseModel):
    take_result: dict[str, Any]
    take_scores: list[int] | None = None


class TranscriptWordDTO(BaseModel):
    word: str
    punctuated_word: str
    start_ms: int
    end_ms: int
    confidence: float


class TranscriptSegmentCreateDTO(BaseModel):
    """실시간 STT 가 확정한 구간 하나. realtime 이 만들어 take service 에 넘긴다."""

    seq: int
    stt_session_no: int
    start_ms: int
    end_ms: int
    transcript: str
    words: list[TranscriptWordDTO]
    confidence: float
    speech_final: bool
    # 이 구간이 확정된 Take 시각 (그 결과를 받기 직전의 마지막 metrics 시각, 없었으면 0)
    final_at_ms: int | None = None


class MetricSecondCreateDTO(BaseModel):
    """WS `metrics` 1건 (take_metric_seconds 한 행). realtime 이 만들어 take service 에 넘긴다."""

    t_ms: int
    slide_number: int | None
    gaze: dict[str, Any] | None
    voice: dict[str, Any] | None
    stt_status: str


class DetailDTO(BaseModel):
    area: str
    slide_number: int | None = None
    text: str
    evidence: dict[str, float] = {}

class StandardResultDTO(BaseModel):
    display: str
    status: bool = False
    target: int
    observed: int

class UserCriteriaDTO(BaseModel):
    display_criteria: list[str] = []
    targets: list[TargetInfoDTO] = []
    script_used: bool | None = None
class TargetInfoDTO(BaseModel):
    display: str | None = None
    metric: str
    operator: str
    value: float

class MissionResultDTO(BaseModel):
    mission_id: uuid.UUID
    status: str
    slide_number: int
    target: TargetInfoDTO
    observed: int
class NextMissionDTO(BaseModel):
    area: str
    slide_number: int
    description: str
    priority: int
class ReviewResultDTO(BaseModel):
    take_id: uuid.UUID
    overall_summary: str
    score: int
    strengths: list[DetailDTO] = []
    improvements: list[DetailDTO] = []
    criteria_results: list[StandardResultDTO] = []
    mission_results: list[MissionResultDTO] = []
    next_missions: list[NextMissionDTO] = []
class ReviewRequestDTO(BaseModel):
    take_result: dict[str, Any]
    script_coverage: dict[str, Any] | None = None
    previous_takes: list[PreviousTakeDTO] | None = None
    previous_recurring_issues: list[ReviewRecurringIssueInputDTO] = []
    missions: list[MissionInputDTO] = []
    user_criteria: UserCriteriaDTO | None = None

class DimensionStatus(BaseModel):
    audience_gaze: str
    script_independence: str
    pacing_rhythm: str
    vocal_delivery: str
    fluency: str
    content_delivery: str

class ScriptModeChangeDTO(BaseModel):
    previous: str
    current: str

class ComparisonDTO(BaseModel):
    metric: str
    slide_number: int | None = None
    previous: float
    current: float
    improved: bool | None = None
    script_mode: ScriptModeChangeDTO | None = None

class ReviewMissionResultDTO(BaseModel):
    mission_id: uuid.UUID
    status: Literal["ACHIEVED", "PARTIAL", "FAILED", "NOT_EVALUABLE"]
    metric: str
    operator: str
    slide_number: int | None = None
    target: float
    observed: float | None = None

class ReviewCriteriaResultDTO(BaseModel):
    display: str
    metric: str
    operator: str
    target: float
    observed: float | None = None
    status: Literal["ACHIEVED", "PARTIAL", "FAILED", "NOT_EVALUABLE"]

# 영역 이름 (docs/ai/architecture.md 6-3). missions.area(String(16)) 에 들어간다
ReviewArea = Literal["GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "CONTENT", "TIME"]

class ReviewNextMissionDTO(BaseModel):
    area: ReviewArea
    slide_number: int | None = None
    description: str = Field(max_length=500)
    target: TargetInfoDTO
    priority: int
    reason: str | None = None

class ReviewRecurringIssueInputDTO(BaseModel):
    area: str
    slide_number: int | None = None
    dimension: str | None = None
class ReviewRecurringIssueDTO(BaseModel):
    area: str
    slide_number: int | None = None
    dimension: Literal[
        "audience_gaze", "script_independence", "pacing_rhythm",
        "vocal_delivery", "fluency", "content_delivery",
    ]

class ReviewMetaDTO(BaseModel):
    schema_version: str
    feature_version: str
    criteria_versions: dict[str, str]
    model: str | None = None

class ReviewResponseDTO(BaseModel):
    take_id: uuid.UUID
    overall_summary: str
    score: int | None = None
    dimensions: DimensionStatus
    strengths: list[DetailDTO] = []
    improvements: list[DetailDTO] = []
    comparison: list[ComparisonDTO] = []
    mission_results: list[ReviewMissionResultDTO] = []
    criteria_results: list[ReviewCriteriaResultDTO] = []
    # 20개 이하 (review-agent.md 5-3, 코칭 계획 입력 한도)
    next_missions: list[ReviewNextMissionDTO] = Field(default=[], max_length=20)
    recurring_issues: list[ReviewRecurringIssueDTO] = []
    fallback_reason: Literal[
        "LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"
        ] | None = None
    meta: ReviewMetaDTO

    @model_validator(mode="after")
    def _next_mission_priorities_unique(self) -> ReviewResponseDTO:
        # missions 의 UQ(source_take_id, priority) 다. 겹치면 한 INSERT 의 ON CONFLICT 가
        # 같은 행을 두 번 고치려 해 PostgreSQL 이 거절한다
        priorities = [mission.priority for mission in self.next_missions]
        if len(priorities) != len(set(priorities)):
            raise ValueError("next_missions 의 priority 가 겹칩니다.")
        return self


# ── 코치 연동 ─────────────────────────────────────────────────────────
# coach 패키지가 코치 요청을 만들 때 쓰는 값. take 모듈의 coach_service 가 DB 에서 읽어 이 모양으로
# 넘기고, 저장할 때도 이 모양으로 받는다 (module 은 coach 를 모른다 — README '의존 방향')


@dataclass(frozen=True)
class SlideTiming:
    """대본 한 장의 시간 계획 (script_slides). 대본 파싱이 값을 주기 전이면 None 이다."""

    slide_number: int
    expected_ms: int | None
    script_chars: int | None


@dataclass(frozen=True)
class MissionInput:
    """이번 Take 에 실린 미션 하나 (review-agent.md 12-1 의 missions)."""

    mission_id: str
    area: str
    slide_number: int | None
    # {metric, operator, value}
    target: Mapping[str, Any] | None
    # 없으면 키를 뺀다. 코치가 1 로 본다 (명시적 null 은 계약 위반이다)
    priority: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class SlideEvent:
    """장을 넘긴 기록 하나 (take_slide_events, WS `slide`).

    `started_ms` 는 그 장으로 넘어온 시각이다."""

    slide_number: int
    started_ms: int


@dataclass(frozen=True)
class MetricSecond:
    """1초 원자료 한 행 (take_metric_seconds). `t_ms` 는 그 `metrics` 의 바깥 시각이다.

    `slide_number` 는 FE 가 보낸 참고값이라 replay 에 쓰지 않는다 (장은 `SlideEvent`)."""

    t_ms: int
    slide_number: int | None
    gaze: Mapping[str, Any] | None
    voice: Mapping[str, Any] | None
    # 그 `metrics` 를 받았을 때의 STT 상태. 마이그레이션 전 행은 None
    stt_status: str | None


@dataclass(frozen=True)
class TranscriptSegment:
    """확정 전사 구간 한 행 (take_transcript_segments). words 는 저장된 모양 그대로."""

    end_ms: int
    words: Sequence[Mapping[str, Any]]
    # 그 구간이 확정된 Take 시각. 마이그레이션 전 행은 None
    final_at_ms: int | None


# 저장할 코치 이벤트 하나: (이벤트 JSON, 그 응답의 meta)
type StoredEvent = tuple[Mapping[str, Any], Mapping[str, Any]]


@dataclass(frozen=True)
class TakeCoachValues:
    """발표 중 코치가 Take 동안 들고 있을 값의 재료 (architecture.md 1-3 1번).

    미션 · 평가 기준 버전 · 동의는 Take 를 시작할 때 고정한 값이다. 반복 문제와 코칭 계획의 바탕
    Take 는 이 Take 보다 앞선 Take 에서 고른다.
    """

    take_id: uuid.UUID
    pitch_id: uuid.UUID
    mode: str
    script_mode: str
    goal_time_sec: int
    # 발표의 제한 시간 · 허용 범위 (초)
    time_limit_sec: int
    lower_deviation_sec: int
    upper_deviation_sec: int
    script_version_id: uuid.UUID
    slides: tuple[SlideTiming, ...]
    # 대본을 파싱할 때 요청한 목표 시간 (script-parser.md 12-2). 없으면 None
    parsed_target_ms: int | None
    missions: tuple[MissionInput, ...]
    recurring_issues: tuple[dict[str, Any], ...]
    # 코칭 계획의 바탕 Take — 이 Take 보다 앞서 가장 늦게 끝난 COMPLETED Take. 첫 Take 면 None
    source_take_id: uuid.UUID | None
    script_used: bool | None
    base_level_db: float | None
    slide_events: tuple[SlideEvent, ...]


@dataclass(frozen=True)
class FinalizeRows:
    """finalize 를 DB 에서 다시 만들 재료 — Take 결과를 기다리는 Take 의 원자료 전부."""

    values: TakeCoachValues
    seconds: tuple[MetricSecond, ...]
    segments: tuple[TranscriptSegment, ...]
    utterance_ends: tuple[int, ...]
    # 받은 순서대로 (coach_events.seq). 이벤트 JSON 그대로
    events: tuple[dict[str, Any], ...]
    raw_data_consent: bool


@dataclass(frozen=True)
class PlanValues:
    """다음 Take 의 코칭 계획 요청 재료 (coach-agent.md 5-2 plan)."""

    pitch_id: uuid.UUID
    time_limit_sec: int
    lower_deviation_sec: int
    upper_deviation_sec: int
    # 파싱이 끝난 가장 최근 대본. 다음 Take 가 쓸 대본이다
    script_version_id: uuid.UUID
    parsed_target_ms: int | None
    slides: tuple[SlideTiming, ...]
    # (장 번호, 그 장 대본)
    scripts: tuple[tuple[int, str], ...]
    # 바탕 Take — 그 발표에서 가장 늦게 끝난 COMPLETED Take (리뷰가 끝내 실패한 Take 포함)
    source_take_id: uuid.UUID | None
    # 리뷰가 있는 Take 중 가장 늦게 끝난 것의 리뷰가 만든 ACTIVE 미션과 반복 문제
    missions: tuple[MissionInput, ...]
    recurring_issues: tuple[dict[str, Any], ...]
    # 바탕 Take 의 Take 결과 · 리뷰 (리뷰가 실패했으면 None)
    previous_take_result: dict[str, Any] | None
    previous_review: dict[str, Any] | None
