"""사용자 의견 쿼리. commit은 service에서 처리한다."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.service_feedback.entity import UserSuggestion
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory


def insert_suggestion(
    db: Session,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    content: str,
    category: SuggestionCategory,
) -> UserSuggestion | None:
    return db.scalar(
        insert(UserSuggestion)
        .values(
            user_id=user_id,
            request_id=request_id,
            content=content,
            source="MY_PAGE",
            category=category.value,
        )
        .on_conflict_do_nothing(constraint="uq_user_suggestions_request")
        .returning(UserSuggestion)
    )


def suggestion_by_request(
    db: Session, user_id: uuid.UUID, request_id: uuid.UUID
) -> UserSuggestion | None:
    return db.scalar(
        select(UserSuggestion).where(
            UserSuggestion.user_id == user_id, UserSuggestion.request_id == request_id
        )
    )
