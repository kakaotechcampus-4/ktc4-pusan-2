
import uuid
from datetime import date

from fastapi import File, UploadFile
from pydantic import BaseModel
from ..take.dto import TakeSummaryDTO


class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    presentation_date: date | None = None

class PitchesDTO(BaseModel):
    pitch_title: str
    pitch_time: int
    thumbnail_url: str | None = None
    takes: TakeSummaryDTO | None = None
class UploadPresentationDTO(BaseModel):
    presentation_file: UploadFile = File(...)
    description: str | None = None

class UploadScriptDTO(BaseModel):
    script_file: UploadFile = File(...)

class UploadResultDTO(BaseModel):
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID
