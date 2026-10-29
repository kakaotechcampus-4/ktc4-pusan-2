from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.dto import SuggestionCreate, SuggestionResponse

suggestions_router = APIRouter(prefix="/suggestions", tags=["suggestions"])
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
