import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from pitch_coach_backend.core.validation import NulFreeStr, reject_unstorable_json

# 발표 시간 상한. FE 의 MAX_TIME_LIMIT_MIN(60분)과 같다. 발표 DTO 도 이 값을 쓴다
MAX_TIME_LIMIT_SEC = 60 * 60


# takes 의 CHECK 와 같은 값이다. FE types/api.ts 의 PracticeMode · ScriptMode 와도 같다
TakeMode = Literal["COACHING", "EXAM"]
ScriptMode = Literal["HIGHLIGHT", "KEYWORD", "OFF"]


class TakeInitRequestDTO(BaseModel):
    mode: TakeMode
    script_mode: ScriptMode
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID
    # FE 는 발표 제한 시간을 그대로 보낸다
    goal_time_sec: int = Field(gt=0, le=MAX_TIME_LIMIT_SEC)

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

class MissionDTO(BaseModel):
    mission_id: uuid.UUID
    slide_number: int
    description:str
    priority: int
    completed: bool

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
