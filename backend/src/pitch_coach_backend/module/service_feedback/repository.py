"""서비스 평가 쿼리. commit과 사용자 잠금은 service에서 처리한다."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.service_feedback.entity import (
    ServiceFeedback,
    ServiceFeedbackImpression,
    UserSuggestion,
)


def feedback_of(db: Session, user_id: uuid.UUID) -> ServiceFeedback | None:
    return db.scalar(select(ServiceFeedback).where(ServiceFeedback.user_id == user_id))


def popup_suggestion(db: Session, feedback_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(UserSuggestion.content).where(UserSuggestion.service_feedback_id == feedback_id)
    )


def display_count(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count())
        .select_from(ServiceFeedbackImpression)
        .where(ServiceFeedbackImpression.user_id == user_id)
    )


def impression_exists(db: Session, user_id: uuid.UUID, request_id: uuid.UUID) -> bool:
    return db.scalar(
        select(
            select(ServiceFeedbackImpression.id)
            .where(
                ServiceFeedbackImpression.user_id == user_id,
                ServiceFeedbackImpression.display_request_id == request_id,
            )
            .exists()
        )
    )


def add_impression(db: Session, user_id: uuid.UUID, request_id: uuid.UUID, number: int) -> None:
    db.add(
        ServiceFeedbackImpression(
            user_id=user_id, display_request_id=request_id, display_number=number
        )
    )


def add_feedback(db: Session, user_id: uuid.UUID, rating: int) -> ServiceFeedback:
    row = ServiceFeedback(user_id=user_id, rating=rating)
    db.add(row)
    db.flush()
    return row


def add_popup_suggestion(db: Session, feedback: ServiceFeedback, content: str) -> None:
    db.add(
        UserSuggestion(
            user_id=feedback.user_id,
            content=content,
            source="RATING_POPUP",
            service_feedback_id=feedback.id,
        )
    )


def insert_suggestion(
    db: Session, user_id: uuid.UUID, request_id: uuid.UUID, content: str
) -> UserSuggestion | None:
    return db.scalar(
        insert(UserSuggestion)
        .values(user_id=user_id, request_id=request_id, content=content, source="MY_PAGE")
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
