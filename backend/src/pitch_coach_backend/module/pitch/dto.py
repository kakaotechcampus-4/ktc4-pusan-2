
import uuid
from datetime import date

from fastapi import File, UploadFile
from pydantic import BaseModel


class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    presentation_date: date | None = None

class UploadPresentationDTO(BaseModel):
    presentation_file: UploadFile = File(...)
    description: str | None = None

class UploadScriptDTO(BaseModel):
    script_file: UploadFile = File(...)

class UploadResultDTO(BaseModel):
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID

class PresentationSummaryDTO(BaseModel):
    id: uuid.UUID
    version: int
    description: str | None = None
    create_at: date

class ScriptSummaryDTO(BaseModel):
    id: uuid.UUID
    version: int
    create_at: date

class EvaluationSummaryDTO(BaseModel):
    id: uuid.UUID
    version: int
    create_at: date
    standards: list[str] | None = None