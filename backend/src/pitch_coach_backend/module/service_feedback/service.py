"""별점은 사용자당 한 번, 의견은 여러 번, 표시 승인은 최대 세 번이다."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.pitch.service import pitch as pitch_service
from pitch_coach_backend.module.service_feedback import repository
from pitch_coach_backend.module.service_feedback.dto import ImpressionResponse, PromptState
from pitch_coach_backend.module.service_feedback.entity import ServiceFeedback, UserSuggestion
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory
from pitch_coach_backend.module.service_feedback.exception import (
    FeedbackAlreadySubmitted,
    FeedbackNotEligible,
    SuggestionRequestConflict,
)
from pitch_coach_backend.module.take.service import take as take_service
from pitch_coach_backend.module.user import service as user_service

DISPLAY_LIMIT = 3


def _has_report(db: Session, user_id: uuid.UUID) -> bool:
    pitch_ids = pitch_service.get_pitch_ids_of_user(db, user_id)
    return take_service.has_completed_report(db, pitch_ids)


@contextmanager
def _locked_user(db: Session, user_id: uuid.UUID) -> Iterator[None]:
    # 인증과 같은 세션에서 이미 시작한 트랜잭션을 사용한다.
    try:
        if not user_service.lock_for_update(db, user_id):
            raise UnauthorizedException()
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_state(db: Session, user_id: uuid.UUID) -> PromptState:
    submitted = repository.feedback_of(db, user_id) is not None
    count = repository.display_count(db, user_id)
    has_report = _has_report(db, user_id)
    return PromptState(
        has_report=has_report,
        submitted=submitted,
        display_count=count,
        should_prompt=has_report and not submitted and count < DISPLAY_LIMIT,
    )


def approve_impression(
    db: Session, user_id: uuid.UUID, request_id: uuid.UUID
) -> ImpressionResponse:
    with _locked_user(db, user_id):
        submitted = repository.feedback_of(db, user_id) is not None
        count = repository.display_count(db, user_id)
        exists = repository.impression_exists(db, user_id, request_id)
        reason = None
        if submitted:
            reason = "ALREADY_SUBMITTED"
        elif not exists and count >= DISPLAY_LIMIT:
            reason = "DISPLAY_LIMIT_REACHED"
        elif not _has_report(db, user_id):
            reason = "NO_REPORT"
        elif not exists:
            repository.add_impression(db, user_id, request_id, count + 1)
            count += 1
        result = ImpressionResponse(
            should_open=reason is None, submitted=submitted, display_count=count, reason=reason
        )
    return result


def submit_feedback(
    db: Session,
    user_id: uuid.UUID,
    *,
    rating: int,
    suggestion: str | None,
    category: SuggestionCategory = SuggestionCategory.OTHER,
) -> tuple[ServiceFeedback, bool]:
    with _locked_user(db, user_id):
        feedback = repository.feedback_of(db, user_id)
        created = feedback is None
        if feedback is not None:
            opinion = repository.popup_suggestion(db, feedback.id)
            saved = None if opinion is None else (opinion.content, opinion.category)
            requested = None if suggestion is None else (suggestion, category.value)
            if feedback.rating != rating or saved != requested:
                raise FeedbackAlreadySubmitted()
        else:
            if not _has_report(db, user_id):
                raise FeedbackNotEligible()
            feedback = repository.add_feedback(db, user_id, rating)
            if suggestion is not None:
                repository.add_popup_suggestion(db, feedback, suggestion, category)
    return feedback, created


def submit_suggestion(
    db: Session,
    user_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    content: str,
    category: SuggestionCategory = SuggestionCategory.OTHER,
) -> tuple[UserSuggestion, bool]:
    try:
        suggestion = repository.insert_suggestion(db, user_id, request_id, content, category)
        created = suggestion is not None
        if suggestion is None:
            suggestion = repository.suggestion_by_request(db, user_id, request_id)
            if suggestion is None or (suggestion.content, suggestion.category) != (
                content,
                category.value,
            ):
                raise SuggestionRequestConflict()
        db.commit()
        return suggestion, created
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "fk_user_suggestions_user" and not user_service.exists(db, user_id):
            raise UnauthorizedException() from None
        raise
    except Exception:
        db.rollback()
        raise
