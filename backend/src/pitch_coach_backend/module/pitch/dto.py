
import uuid
from datetime import date
from enum import StrEnum

from fastapi import File, UploadFile
from pydantic import BaseModel
from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from typing import Protocol

class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    upper_deviation: int = 0
    lower_deviation: int = 0
    presentation_date: date | None = None

class PitchesDTO(BaseModel):
    pitch_id: uuid.UUID
    pitch_title: str
    pitch_time: int
    thumbnail_url: str | None = None
    pitch_deadline: date
    takes: list[TakeSummaryDTO]

class AllPitchesDTO(BaseModel):
    pitches: list[PitchesDTO]
      
class UploadPresentationDTO(BaseModel):
    presentation_file: UploadFile = File(...)

class UploadScriptDTO(BaseModel):
    script_file: UploadFile = File(...)

class UploadPresentationResultDTO(BaseModel):
    pitch_id: uuid.UUID
    presentation_version_id: uuid.UUID
    file_url: str


class ScriptParseErrorCode(StrEnum):
    """파싱이 FAILED 가 된 이유. FE 응답의 error_code 이자 script_versions.parse_error 값.

    FE 는 코드마다 다르게 할 필요 없이 "다시 시도" 하나로 받으면 된다. 코드는 원인 추적용이다.
    """

    AI_TIMEOUT = "AI_TIMEOUT"  # AI 가 제한 시간 안에 답하지 않음
    AI_UNAVAILABLE = "AI_UNAVAILABLE"  # AI 서버에 연결할 수 없음
    AI_ERROR = "AI_ERROR"  # AI 가 5xx·429·408 을 줌
    AI_REJECTED = "AI_REJECTED"  # AI 가 4xx 로 입력을 거절함
    AI_INVALID_OUTPUT = "AI_INVALID_OUTPUT"  # 응답이 계약과 다름
    PARSE_EXPIRED = "PARSE_EXPIRED"  # 요청 후 한참 지나도 안 끝남 (BE 재시작 등)
    INTERNAL_ERROR = "INTERNAL_ERROR"  # BE 쪽 예상 못 한 오류
    # 파싱 기능 전에 올라온 대본 (migration 이 채움). 원문이 DB 에 없어 다시 올려야 한다
    LEGACY_UNPARSED = "LEGACY_UNPARSED"

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

class PresentationDetailDTO(BaseModel):
    pitch_id: uuid.UUID
    presentation_version_id: uuid.UUID
    version: int
    file_url: str
    description: str | None = None
    created_at: date
class StandardTextDTO(BaseModel):
    standard_text: str

class StandardDTO(BaseModel):
    standard: str
      
class StandardTextResponseDTO(BaseModel):
    pitch_id: uuid.UUID
    standards: list[StandardDTO]
    except_standard: str | None = None
    
