from pydantic import BaseModel

class TakeInitRequestDTO(BaseModel):
    mode: str
    script_mode: str
    presentation_version_id: str
    script_version_id: str

class TakeUpdateRequestDTO(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None
    event_logs: list[dict] | None = None

class CalibrationDTO(BaseModel):
    face_detected: bool = False
    mic_detected: bool = False
    base_volume: float = 0.0
    gaze_confidence: bool = False