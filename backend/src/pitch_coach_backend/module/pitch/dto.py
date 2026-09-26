
import uuid
from datetime import date

from fastapi import File, UploadFile
from pydantic import BaseModel
from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from typing import Protocol

class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    presentation_date: date | None = None

class PitchesDTO(BaseModel):
    pitch_id: uuid.UUID
    pitch_title: str
    pitch_time: int
    thumbnail_url: str | None = None
    takes: list[TakeSummaryDTO]

class AllPitchesDTO(BaseModel):
    pitches: list[PitchesDTO]
      
class UploadPresentationDTO(BaseModel):
    presentation_file: UploadFile = File(...)
    description: str | None = None

class UploadScriptDTO(BaseModel):
    script_file: UploadFile = File(...)

class UploadResultDTO(BaseModel):
    presentation_version_id: uuid.UUID
    script_version_id: uuid.UUID

class VersionDTO(BaseModel):
    id: uuid.UUID
    version: int

class VersionSummaryDTO(BaseModel):
    pitch_id: uuid.UUID
    presentation_versions: list[VersionDTO]
    script_versions: list[VersionDTO]
    evaluation_versions: list[VersionDTO]

class Versioned(Protocol):
    id: uuid.UUID
    version: int