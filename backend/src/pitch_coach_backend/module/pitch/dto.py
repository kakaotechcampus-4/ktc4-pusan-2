
from datetime import date
from fastapi import UploadFile
from fastapi import File
from pydantic import BaseModel

class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    presentation_date: date | None = None

class UploadPresentationDTO(BaseModel):
    pitch_id: str
    user_id: str
    presentation_file: UploadFile = File(...)
    description: str | None = None

class UploadScriptDTO(BaseModel):
    pitch_id: str
    user_id: str
    script_file: UploadFile = File(...)
