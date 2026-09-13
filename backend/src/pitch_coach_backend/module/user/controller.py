from fastapi import APIRouter

from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.user.dto import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse, summary="내 정보")
def read_me(current_user: CurrentUser) -> UserResponse:
    """Access JWT 로 인증한다. entity 를 그대로 돌려주고 response_model 이 변환한다."""
    return current_user
