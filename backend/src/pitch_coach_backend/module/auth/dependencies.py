"""Depends 로 주입하는 인증 함수.

get_current_user 는 Access JWT 검증과 사용자 존재 확인만 한다.
활성 상태 컬럼을 두지 않기로 했으므로 계정 정지 같은 판단은 여기서 하지 않는다.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import constant_time_equals, decode_access_token
from pitch_coach_backend.module.auth.exception import CsrfValidationFailed
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# auto_error=False: 헤더가 없을 때 FastAPI 의 403 대신 우리 401 을 내보낸다
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise UnauthorizedException()

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as e:
        raise UnauthorizedException("유효하지 않은 토큰입니다.") from e

    user = user_service.find(db, user_id)
    if user is None:
        # 토큰은 멀쩡한데 사용자가 없다. 탈퇴한 계정의 토큰이 남아 있는 경우다.
        raise UnauthorizedException("유효하지 않은 토큰입니다.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _allowed_origins() -> set[str]:
    return {settings.frontend_base_url.rstrip("/")}


def verify_csrf(
    request: Request,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    """쿠키로 인증하는 엔드포인트(refresh·logout)의 CSRF 방어.

    쿠키는 브라우저가 자동으로 붙이므로, 쿠키만으로 인증하면 다른 사이트가
    사용자를 시켜 우리 API 를 호출할 수 있다. 두 겹으로 막는다.

    1. Origin 대조 — 헤더가 있으면 허용 목록과 같아야 한다
    2. double-submit — JS 가 읽을 수 있는 csrf_token 쿠키를 헤더에도 실어 보낸다.
       다른 출처의 스크립트는 우리 쿠키를 읽지 못하므로 헤더를 만들 수 없다.

    Origin 은 없을 수 있어서(일부 same-origin 요청) 2번이 실제 방어선이다.
    """
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in _allowed_origins():
        raise CsrfValidationFailed()

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token or not csrf_header:
        raise CsrfValidationFailed()

    if not constant_time_equals(cookie_token, csrf_header):
        raise CsrfValidationFailed()


CsrfProtected = Depends(verify_csrf)
