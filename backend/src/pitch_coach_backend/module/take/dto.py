import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

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
class MissionDTO(BaseModel):
    mission_id: uuid.UUID
    slide_number: int
    description:str
    priority: int
    completed: bool

class PreviousTakeDTO(BaseModel):
    take_result: dict[str, Any]
    take_scores: list[int] | None = None
class PreviousMissionsDTO(BaseModel):
    # Take 가 아직 없으면 None (next_take_number 는 1, missions 는 빈 목록)
    source_take_id: uuid.UUID | None
    next_take_number: int
    missions: list[MissionDTO]


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

class ReviewNextMissionDTO(BaseModel):
    area: str
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
    next_missions: list[ReviewNextMissionDTO] = []
    recurring_issues: list[ReviewRecurringIssueDTO] = []
    fallback_reason: Literal[
        "LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"
        ] | None = None
    meta: ReviewMetaDTO



