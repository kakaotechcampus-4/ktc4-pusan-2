from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.dto import (
    FeedbackCreate,
    FeedbackResponse,
    ImpressionCreate,
    ImpressionResponse,
    PromptState,
    SuggestionCreate,
    SuggestionResponse,
)

router = APIRouter(prefix="/service-feedback", tags=["service-feedback"])
suggestions_router = APIRouter(prefix="/suggestions", tags=["suggestions"])
DB = Annotated[Session, Depends(get_db)]


@router.get("/me", response_model=PromptState, summary="서비스 평가·팝업 상태")
def get_state(current_user: CurrentUser, db: DB, response: Response) -> PromptState:
    response.headers["Cache-Control"] = "no-store"
    return service.get_state(db, current_user.id)


@router.post("/impressions", response_model=ImpressionResponse, summary="평점 팝업 표시 승인")
def approve_impression(
    body: ImpressionCreate, current_user: CurrentUser, db: DB, response: Response
) -> ImpressionResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.approve_impression(db, current_user.id, body.display_request_id)


@router.post("", response_model=FeedbackResponse, status_code=201, summary="서비스 평점 보내기")
def submit_feedback(body: FeedbackCreate, current_user: CurrentUser, db: DB, response: Response):
    feedback, created = service.submit_feedback(
        db, current_user.id, rating=body.rating, suggestion=body.suggestion
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return feedback


@suggestions_router.post(
    "", response_model=SuggestionResponse, status_code=201, summary="의견 보내기"
)
def submit_suggestion(
    body: SuggestionCreate, current_user: CurrentUser, db: DB, response: Response
):
    suggestion, created = service.submit_suggestion(
        db, current_user.id, request_id=body.request_id, content=body.content
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return suggestion
