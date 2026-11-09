"""Take별 설문 · 사용자 의견 쿼리. commit은 service에서 처리한다."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.service_feedback.entity import TakeSurvey, UserSuggestion
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


def insert_survey(
    db: Session,
    user_id: uuid.UUID,
    take_id: uuid.UUID,
    *,
    accuracy_rating: int,
    usefulness_rating: int,
    comment: str | None,
    question_version: int,
) -> TakeSurvey | None:
    """이미 있으면 None. 같은 Take 를 동시에 보내도 한 행만 남는다."""
    return db.scalar(
        insert(TakeSurvey)
        .values(
            user_id=user_id,
            take_id=take_id,
            accuracy_rating=accuracy_rating,
            usefulness_rating=usefulness_rating,
            comment=comment,
            question_version=question_version,
        )
        .on_conflict_do_nothing(constraint="uq_take_surveys_take")
        .returning(TakeSurvey)
    )


def survey_of(db: Session, take_id: uuid.UUID) -> TakeSurvey | None:
    return db.scalar(select(TakeSurvey).where(TakeSurvey.take_id == take_id))


def surveyed_take_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(TakeSurvey.take_id).where(
                TakeSurvey.user_id == user_id, TakeSurvey.take_id.is_not(None)
            )
        )
    )
