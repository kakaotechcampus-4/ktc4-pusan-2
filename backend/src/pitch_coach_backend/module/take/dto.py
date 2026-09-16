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

class MissionDTO(BaseModel):
    mission_id: uuid.UUID
    slide_number: int
    description:str
    priority: int
    completed: bool

class PreviousMissionsDTO(BaseModel):
    source_take_id: uuid.UUID
    next_take_number: int
    missions: list[MissionDTO]