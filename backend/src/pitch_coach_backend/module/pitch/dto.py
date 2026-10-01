
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from fastapi import File, UploadFile
from pydantic import BaseModel, field_validator
from pitch_coach_backend.module.pitch.entity import ScriptParseStatus
from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from typing import Protocol

class PitchDTO(BaseModel):
    title: str
    time_limit_sec: int
    upper_deviation: int = 0
    lower_deviation: int = 0
    presentation_date: date | None = None

# 수정에 사용할 DTO
class PitchUpdateDTO(BaseModel):
    title: str | None = None
    time_limit_sec: int | None = None
    upper_deviation: int | None = None
    lower_deviation: int | None = None
    presentation_date: date | None = None

    @field_validator("time_limit_sec", "title", "upper_deviation", "lower_deviation")
    @classmethod
    def not_null(cls, v):
        if v is None:
            raise ValueError("Field cannot be null")
        return v

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

class UploadPresentationResultDTO(BaseModel):
    pitch_id: uuid.UUID
    presentation_version_id: uuid.UUID
    file_url: str


class ScriptCreateDTO(BaseModel):
    """대본 한 버전. FE 는 textarea 내용을 그대로 보낸다 (JSON 이라 인코딩 걱정이 없다).

    길이·빈 값·NUL 검사는 service.validate_script_text 가 한다 — 여기서 pydantic 으로 막으면
    422 VALIDATION_ERROR 로 나가 다른 대본 오류(400 INVALID_SCRIPT)와 코드가 갈린다.
    """

    content: str


class ScriptCreatedDTO(BaseModel):
    script_version_id: uuid.UUID
    version: int
    parse_status: ScriptParseStatus


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


@dataclass(frozen=True)
class ParseTicket:
    """백그라운드 파싱 작업에 넘기는 것. 작업은 DB 세션 없이 이것만 들고 AI 를 기다린다.

    requested_at 은 이 작업의 표식이다. 저장할 때 DB 의 parse_requested_at 과 같아야만
    반영되므로, 재시도가 새로 시작된 뒤 늦게 끝난 옛 작업은 아무것도 덮지 못한다.
    """

    script_version_id: uuid.UUID
    requested_at: datetime
    script_text: str


class HighlightDTO(BaseModel):
    """content 안의 문자 위치. end 는 포함하지 않는다 (content[start:end])."""

    start: int
    end: int


class ScriptSlideDTO(BaseModel):
    slide_number: int
    content: str
    keywords: list[str]
    highlights: list[HighlightDTO]


class ScriptDetailDTO(BaseModel):
    """폴링 응답. 상태와 상관없이 같은 키로 나가서 FE 가 타입 하나로 받는다."""

    script_version_id: uuid.UUID
    version: int
    # 업로드한 원문. 편집 화면에 다시 띄울 때 쓴다. 파싱 기능 전에 올라온 대본은 null.
    # slides[].content(AI 가 다듬은 슬라이드 본문)와 헷갈리지 않게 이름을 따로 둔다
    original_content: str | None
    parse_status: ScriptParseStatus
    # DONE 일 때만 값이 있다. False 는 구분자가 없어 대본 전체를 한 슬라이드로 둔 정상 결과
    segmented: bool | None
    slides: list[ScriptSlideDTO]
    terms: list[str]
    error_code: ScriptParseErrorCode | None


class ParseRequestedDTO(BaseModel):
    script_version_id: uuid.UUID
    parse_status: ScriptParseStatus

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
    
