import uuid

from pydantic import BaseModel

class TakeInitRequestDTO(BaseModel):
    mode: str
    script_mode: str
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID

class TakeUpdateRequestDTO(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None
    event_logs: list[dict] | None = None

class CalibrationDTO(BaseModel):
    face_detected: bool = False
    mic_detected: bool = False
    base_volume: float = 0.0
    gaze_confidence: bool = False


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
