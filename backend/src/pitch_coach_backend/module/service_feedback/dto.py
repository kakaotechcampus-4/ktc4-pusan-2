import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pitch_coach_backend.core.limits import INT4_MAX
from pitch_coach_backend.core.validation import reject_nul
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory

SuggestionSource = Literal["MY_PAGE"]
# DB 제약(`ck_user_suggestions_content`)과 같은 값이다
MAX_CONTENT_CHARS = 2000


def normalize_suggestion(value: str | None) -> str | None:
    if value is None:
        return None
    reject_nul(value)
    value = value.strip()
    if len(value) > MAX_CONTENT_CHARS:
        raise ValueError(f"의견은 {MAX_CONTENT_CHARS}자까지 보낼 수 있습니다.")
    return value or None


class SuggestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    content: str
    category: SuggestionCategory = SuggestionCategory.OTHER

    @field_validator("content")
    @classmethod
    def nonempty_content(cls, value: str) -> str:
        normalized = normalize_suggestion(value)
        if normalized is None:
            raise ValueError("의견 내용을 입력해 주세요.")
        return normalized


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: SuggestionSource
    category: SuggestionCategory
    created_at: datetime


Rating = Annotated[int, Field(strict=True, ge=1, le=5)]


class TakeSurveyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accuracy_rating: Rating
    usefulness_rating: Rating
    # 생략 · null · 공백만이면 남기지 않은 것으로 본다
    comment: str | None = None
    question_version: Annotated[int, Field(strict=True, ge=1, le=INT4_MAX)] = 1

    _normalize = field_validator("comment")(normalize_suggestion)


class TakeSurveyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    take_id: uuid.UUID
    accuracy_rating: int
    usefulness_rating: int
    comment: str | None
    question_version: int
    created_at: datetime


class TakeSurveyState(BaseModel):
    """리포트 아래 설문 버튼이 쓴다. 제출하지 않았으면 survey 가 null 이다."""

    submitted: bool
    survey: TakeSurveyResponse | None


class PendingSurvey(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    take_id: uuid.UUID
    pitch_id: uuid.UUID
    pitch_title: str
    take_number: int
    completed_at: datetime


class PendingSurveys(BaseModel):
    """홈 알림이 쓴다. 테스트 참여에 동의한 뒤 끝난 Take 중 설문을 남기지 않은 것."""

    items: list[PendingSurvey]
