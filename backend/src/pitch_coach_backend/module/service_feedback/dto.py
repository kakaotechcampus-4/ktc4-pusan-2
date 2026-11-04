import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pitch_coach_backend.core.validation import reject_nul
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory

SuggestionSource = Literal["RATING_POPUP", "MY_PAGE"]
PromptReason = Literal["ALREADY_SUBMITTED", "NO_REPORT", "DISPLAY_LIMIT_REACHED"]


def normalize_suggestion(value: str | None) -> str | None:
    if value is None:
        return None
    reject_nul(value)
    value = value.strip()
    if len(value) > 2000:
        raise ValueError("의견은 2000자까지 보낼 수 있습니다.")
    return value or None


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Annotated[int, Field(strict=True, ge=1, le=5)]
    suggestion: str | None = None
    category: SuggestionCategory = SuggestionCategory.OTHER

    _normalize = field_validator("suggestion")(normalize_suggestion)


class ImpressionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_request_id: uuid.UUID


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


class PromptState(BaseModel):
    has_report: bool
    submitted: bool
    display_count: int
    display_limit: int = 3
    should_prompt: bool


class ImpressionResponse(BaseModel):
    should_open: bool
    submitted: bool
    display_count: int
    display_limit: int = 3
    reason: PromptReason | None = None


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    submitted: bool = True
    rating: int
    created_at: datetime


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: SuggestionSource
    category: SuggestionCategory
    created_at: datetime
