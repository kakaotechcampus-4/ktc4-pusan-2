"""관리자 콘솔 (/admin).

화면은 static/ 의 HTML · JS · CSS 세 파일이고, 데이터는 /admin/api/* 에서 받는다. 전부 같은
로그인(require_admin) 뒤에 있다. 앱 API 가 아니라서 /api 밖에 두고, /api/docs 에도 싣지 않는다.
/admin 은 Caddy 가 /admin/ 으로 보낸다 — 여기서 보내면 uvicorn 이 https 를 몰라 http 주소가 된다.
"""

import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.admin import service
from pitch_coach_backend.module.admin.dependencies import require_admin
from pitch_coach_backend.module.admin.dto import (
    AdminFeedbackGroupRow,
    AdminFeedbackRow,
    AdminPage,
    AdminRatingSummary,
    AdminSuggestionRow,
    AdminTakeRow,
    AdminUserRow,
    StatsResponse,
)
from pitch_coach_backend.module.service_feedback.dto import SuggestionSource
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory

STATIC_DIR = Path(__file__).parent / "static"

# takes 의 CHECK 와 같은 값이다
TakeStatus = Literal["READY", "RUNNING", "ANALYZING", "COMPLETED", "FAILED"]

router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)


@router.get("/")
def console() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/app.js")
def console_script() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js")


@router.get("/style.css")
def console_style() -> FileResponse:
    return FileResponse(STATIC_DIR / "style.css")


@router.get("/api/auth", status_code=status.HTTP_204_NO_CONTENT)
def auth() -> Response:
    """로그인 확인만 한다. Caddy 가 서버 로그(/admin/logs)를 넘기기 전에 묻는다 (forward_auth)."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/stats")
def stats(db: Annotated[Session, Depends(get_db)]) -> StatsResponse:
    return service.get_stats(db)


@router.get("/api/users")
def users(db: Annotated[Session, Depends(get_db)]) -> list[AdminUserRow]:
    return service.list_users(db)


@router.get("/api/takes")
def takes(
    db: Annotated[Session, Depends(get_db)],
    status: TakeStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AdminTakeRow]:
    return service.list_takes(db, status=status, limit=limit)


@router.get("/api/takes/stuck")
def stuck_takes(db: Annotated[Session, Depends(get_db)]) -> list[AdminTakeRow]:
    return service.list_stuck_takes(db)


@router.get("/api/service-feedbacks", response_model=AdminPage[AdminFeedbackRow])
def feedbacks(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminPage[AdminFeedbackRow]:
    return service.list_feedbacks(db, limit=limit, offset=offset)


@router.get("/api/service-feedbacks/summary")
def rating_summary(db: Annotated[Session, Depends(get_db)]) -> AdminRatingSummary:
    return service.rating_summary(db)


@router.get("/api/service-feedbacks/by-user", response_model=AdminPage[AdminFeedbackGroupRow])
def feedbacks_by_user(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminPage[AdminFeedbackGroupRow]:
    return service.list_feedback_groups(db, limit=limit, offset=offset)


@router.get("/api/suggestions", response_model=AdminPage[AdminSuggestionRow])
def suggestions(
    db: Annotated[Session, Depends(get_db)],
    source: SuggestionSource | None = None,
    category: SuggestionCategory | None = None,
    user_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminPage[AdminSuggestionRow]:
    return service.list_suggestions(
        db, source=source, category=category, limit=limit, offset=offset, user_id=user_id
    )
