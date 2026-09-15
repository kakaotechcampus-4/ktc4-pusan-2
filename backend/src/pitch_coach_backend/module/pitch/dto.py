
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
