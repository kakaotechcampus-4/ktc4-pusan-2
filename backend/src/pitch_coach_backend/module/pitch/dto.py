
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Protocol

from fastapi import File, UploadFile
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from pitch_coach_backend.core.validation import reject_nul
from pitch_coach_backend.module.pitch.entity import ScriptParseStatus
from pitch_coach_backend.module.take.dto import MAX_TIME_LIMIT_SEC, ScriptMode, TakeSummaryDTO

# FE 의 허용 오차 상한(MAX_TOLERANCE_SEC)과 같다
MAX_DEVIATION_SEC = 5 * 60


# 생성·수정이 같이 쓴다. 범위를 벗어나면 DB 에러(500) 대신 422
class PitchSaveRequestDTO(BaseModel):
    # DB 는 String(50). FE 도 50자에서 입력을 막는다 (MAX_TITLE_CHARS)
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
        AfterValidator(reject_nul),
    ]
    time_limit_sec: int = Field(gt=0, le=MAX_TIME_LIMIT_SEC)
    upper_deviation: int = Field(default=0, ge=0, le=MAX_DEVIATION_SEC)
    lower_deviation: int = Field(default=0, ge=0, le=MAX_DEVIATION_SEC)
    presentation_date: date | None = None

class PitchListItemDTO(BaseModel):
    pitch_id: uuid.UUID
    pitch_title: str
    pitch_time: int
    thumbnail_url: str | None = None
    # 발표일은 생성 때 비워 둘 수 있다 (PitchSaveRequestDTO.presentation_date)
    presentation_date: date | None = None
    takes: list[TakeSummaryDTO]

class PitchListResponseDTO(BaseModel):
    pitches: list[PitchListItemDTO]
      
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
    # 발표 제한 시간(ms). AI 가 장별 예상 시간을 이 합에 맞춰 나눈다. parse_meta 에도 남긴다
    target_ms: int | None


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

class NewerScriptDTO(BaseModel):
    """연습에 쓸 대본보다 새로 올렸지만 아직 쓸 수 없는 대본. FE 가 왜 옛 대본인지 알린다."""

    id: uuid.UUID
    version: int
    # PENDING(나누는 중) 또는 FAILED(나누지 못함). 오래된 PENDING 은 FAILED 로 내린다
    parse_status: ScriptParseStatus
    error_code: ScriptParseErrorCode | None

class PrepareDTO(BaseModel):
    """장치 점검 화면이 Take 를 만들 때 쓰는 값. 피치 생성을 거치지 않고 들어와도 버전을 안다."""

    pitch_id: uuid.UUID
    title: str
    time_limit_sec: int
    # 가장 최근 발표자료. 없으면 None
    presentation: VersionDTO | None
    # 파싱이 끝난 가장 최근 대본. 슬라이드가 없으면 리허설을 열 수 없어 PENDING · FAILED 는 건너뛴다
    script: VersionDTO | None
    # script 보다 새 대본이 있는데 파싱 중이거나 실패했으면 그 대본. 없으면 None
    newer_script: NewerScriptDTO | None
    # 지금 기준 다음 번호. 그사이 다른 Take 가 생기면 달라져서 실제 번호는 Take 생성 응답을 본다
    next_take_number: int
    # 가장 최근 Take 의 대본 표시 방식. Take 가 없으면 HIGHLIGHT
    default_script_mode: ScriptMode

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

class StandardParseResponseDTO(BaseModel):
    standards: list[str]
    except_standard: str | None = None
class StandardTextResponseDTO(BaseModel):
    pitch_id: uuid.UUID
    standards: list[StandardDTO]
    except_standard: str | None = None
    
