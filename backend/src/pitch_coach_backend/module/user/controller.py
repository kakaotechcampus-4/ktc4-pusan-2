from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.auth.controller import clear_session_cookies
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.service import (
    delete_pitch_files,
    get_pitch_ids_of_user,
    get_take_ids_of_user,
)
from pitch_coach_backend.module.user import service
from pitch_coach_backend.module.user.dto import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse, summary="내 정보")
def read_me(current_user: CurrentUser) -> UserResponse:
    """Access JWT 로 인증한다. entity 를 그대로 돌려주고 response_model 이 변환한다."""
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="회원 탈퇴")
def withdraw(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    background_tasks: BackgroundTasks,
) -> None:
    """회원과 그 데이터를 바로 지운다. 되살릴 수 없다.

    이미 발급된 Access 는 사용자가 없어 401 이 난다.
    Bearer 헤더로 인증하므로 CSRF 검사는 하지 않는다.
    """
    # 지운 뒤에는 CASCADE 로 사라져 읽을 수 없다
    pitch_ids = get_pitch_ids_of_user(db, current_user.id)
    take_ids = get_take_ids_of_user(db, current_user.id)
    service.delete(db, current_user)
    # 발표 삭제와 같다. 진행 중이던 Take 의 실시간 STT 를 끊고, 발표자료 파일은 응답 뒤에 지운다
    hooks.live_takes().discard(take_ids)
    for pitch_id in pitch_ids:
        background_tasks.add_task(delete_pitch_files, pitch_id)
    clear_session_cookies(response)
