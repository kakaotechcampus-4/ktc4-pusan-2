import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field


class TakeInitRequestDTO(BaseModel):
    mode: str
    script_mode: str
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID
    goal_time_sec: int = Field(gt=0)

# 보낸 필드만 반영한다 (service 가 exclude_none 으로 읽는다).
# 시각은 시간대가 있어야 한다. 없으면 DB 의 시각과 앞뒤를 비교할 수 없다
class TakeUpdateRequestDTO(BaseModel):
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    event_logs: list[dict] | None = None

class TakeSummaryDTO(BaseModel):
    take_id: uuid.UUID
    take_version: int
    take_elapsed: int | None = None
    take_time: int
    script_mode: str
    score: int | None = None
    delta: int | None = None
    created_at: datetime | None = None

class CalibrationResultDTO(BaseModel):
    engine_version: str | None = None
    status: str | None = None
    separability: float | None = None
    loo_accuracy: float | None = None
    warnings: list[str] | None = None

class CalibrationDTO(BaseModel):
    base_level_db: float | None = None
    result: CalibrationResultDTO | None = None
    face_detected: bool = False
    mic_detected: bool = False
    # DB 는 Numeric(5, 2) — 999.99 까지
    base_volume: float = Field(default=0.0, ge=0, lt=1000)
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
