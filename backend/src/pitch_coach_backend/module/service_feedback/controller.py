import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.dto import (
    PendingSurveys,
    SuggestionCreate,
    SuggestionResponse,
    TakeSurveyCreate,
    TakeSurveyResponse,
    TakeSurveyState,
)

suggestions_router = APIRouter(prefix="/suggestions", tags=["suggestions"])
surveys_router = APIRouter(tags=["take-surveys"])
DB = Annotated[Session, Depends(get_db)]


@suggestions_router.post(
    "", response_model=SuggestionResponse, status_code=201, summary="의견 보내기"
)
def submit_suggestion(
    body: SuggestionCreate, current_user: CurrentUser, db: DB, response: Response
):
    suggestion, created = service.submit_suggestion(
        db,
        current_user.id,
        request_id=body.request_id,
        content=body.content,
        category=body.category,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return suggestion


@surveys_router.post(
    "/takes/{take_id}/survey",
    response_model=TakeSurveyResponse,
    status_code=201,
    summary="리포트 설문 보내기",
)
def submit_survey(
    take_id: uuid.UUID,
    body: TakeSurveyCreate,
    current_user: CurrentUser,
    db: DB,
    response: Response,
):
    survey, created = service.submit_survey(db, current_user.id, take_id, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return survey


@surveys_router.get(
    "/takes/{take_id}/survey", response_model=TakeSurveyState, summary="리포트 설문 제출 여부"
)
def get_survey(
    take_id: uuid.UUID, current_user: CurrentUser, db: DB, response: Response
) -> TakeSurveyState:
    response.headers["Cache-Control"] = "no-store"
    return service.get_survey(db, current_user.id, take_id)


@surveys_router.get(
    "/take-surveys/pending", response_model=PendingSurveys, summary="설문을 남기지 않은 리포트"
)
def pending_surveys(current_user: CurrentUser, db: DB, response: Response) -> PendingSurveys:
    response.headers["Cache-Control"] = "no-store"
    return service.pending_surveys(db, current_user.id, consented_at=current_user.test_consent_at)
