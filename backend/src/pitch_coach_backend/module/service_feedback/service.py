"""Take별 설문은 리포트마다 한 번, 마이페이지 의견은 여러 번 보낼 수 있다.

둘 다 같은 요청의 재전송은 한 건으로 친다.
"""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.service_feedback import repository
from pitch_coach_backend.module.service_feedback.dto import (
    PendingSurvey,
    PendingSurveys,
    TakeSurveyCreate,
    TakeSurveyResponse,
    TakeSurveyState,
)
from pitch_coach_backend.module.service_feedback.entity import TakeSurvey, UserSuggestion
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory
from pitch_coach_backend.module.service_feedback.exception import (
    SuggestionRequestConflict,
    TakeSurveyAlreadySubmitted,
    TakeSurveyNotEligible,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import take as take_service
from pitch_coach_backend.module.user import service as user_service


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


def _answers(survey: TakeSurvey | TakeSurveyCreate) -> tuple:
    return (
        survey.accuracy_rating,
        survey.usefulness_rating,
        survey.comment,
        survey.question_version,
    )


def submit_survey(
    db: Session, user_id: uuid.UUID, take_id: uuid.UUID, body: TakeSurveyCreate
) -> tuple[TakeSurvey, bool]:
    """설문을 저장한다. 같은 답을 다시 보내면 저장한 것을 돌려주고 다른 답이면 409 다."""
    try:
        take = take_service.find_owned(db, take_id, user_id)
        if take is None:
            raise NonExistentTake()
        if not take_service.has_report(db, take):
            raise TakeSurveyNotEligible()
        survey = repository.insert_survey(
            db,
            user_id,
            take_id,
            accuracy_rating=body.accuracy_rating,
            usefulness_rating=body.usefulness_rating,
            comment=body.comment,
            question_version=body.question_version,
        )
        created = survey is not None
        if survey is None:
            survey = repository.survey_of(db, take_id)
            if survey is None or _answers(survey) != _answers(body):
                raise TakeSurveyAlreadySubmitted()
        db.commit()
        return survey, created
    except IntegrityError as exc:
        db.rollback()
        # 확인한 뒤 저장하기 전에 탈퇴했거나 Take 를 지운 경우다
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "fk_take_surveys_user" and not user_service.exists(db, user_id):
            raise UnauthorizedException() from None
        if constraint == "fk_take_surveys_take":
            raise NonExistentTake() from None
        raise
    except Exception:
        db.rollback()
        raise


def get_survey(db: Session, user_id: uuid.UUID, take_id: uuid.UUID) -> TakeSurveyState:
    if take_service.find_owned(db, take_id, user_id) is None:
        raise NonExistentTake()
    survey = repository.survey_of(db, take_id)
    return TakeSurveyState(
        submitted=survey is not None,
        survey=None if survey is None else TakeSurveyResponse.model_validate(survey),
    )


def pending_surveys(
    db: Session, user_id: uuid.UUID, *, consented_at: datetime | None
) -> PendingSurveys:
    """동의한 뒤 끝난 Take 만 넣는다. 그 전 연습까지 넣으면 처음 들어온 홈이 알림으로 가득 찬다."""
    if consented_at is None:
        return PendingSurveys(items=[])
    rows = take_service.list_reported_takes(
        db,
        user_id,
        completed_after=consented_at,
        exclude=repository.surveyed_take_ids(db, user_id),
    )
    return PendingSurveys(items=[PendingSurvey.model_validate(row) for row in rows])
