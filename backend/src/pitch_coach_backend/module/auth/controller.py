"""인증 엔드포인트. HTTP 관심사(쿠키·리다이렉트·헤더)만 다루고 로직은 service 에 맡긴다.

엔드포인트는 네 개뿐이다. Access JWT 를 얻는 경로는 refresh 하나로 통일한다.

    GET  /api/auth/google/start     구글 인증 화면으로 보낸다
    GET  /api/auth/google/callback  구글이 돌아오는 곳. 쿠키를 심고 프론트로 보낸다
    POST /api/auth/refresh          쿠키의 Refresh 로 Access 를 발급 (+ Refresh 회전)
    POST /api/auth/logout           현재 세션의 Refresh 폐기

쿠키가 셋이다.
    refresh_token   HttpOnly. Path=/api/auth — 다른 API 요청에는 실려 나가지 않는다
    csrf_token      JS 가 읽어 헤더에 실어야 하므로 HttpOnly 가 아니다
    oauth_browser   start~callback 동안만 사는 임시 쿠키
"""

import secrets
from typing import Annotated
from urllib.parse import urljoin

import redis
from fastapi import APIRouter, Cookie, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.core.redis import get_redis
from pitch_coach_backend.module.auth import service
from pitch_coach_backend.module.auth.dependencies import (
    CSRF_COOKIE_NAME,
    CsrfProtected,
)
from pitch_coach_backend.module.auth.dto import LogoutResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
BROWSER_COOKIE_NAME = "oauth_browser"

# Refresh 쿠키는 인증 엔드포인트에만 실린다. /api/pitches 같은 요청에 함께 나가지 않아
# 유출 표면이 줄어든다. csrf_token 은 프론트(/)에서 읽어야 해서 Path=/ 다.
REFRESH_COOKIE_PATH = "/api/auth"
CSRF_COOKIE_PATH = "/"

# 로그인 시작부터 콜백까지만 살면 된다. state 의 TTL 과 맞춘다.
BROWSER_COOKIE_MAX_AGE = 600


def _set_session_cookies(response: Response, issued: service.IssuedSession) -> str:
    """Refresh·CSRF 쿠키를 심고 새 CSRF 토큰을 돌려준다. 회전할 때마다 둘 다 갱신한다."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        issued.refresh_token,
        max_age=service.refresh_cookie_max_age(),
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=service.refresh_cookie_max_age(),
        path=CSRF_COOKIE_PATH,
        # httponly=False 가 의도다. 프론트가 읽어 X-CSRF-Token 헤더에 넣는다.
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return csrf_token


def _clear_session_cookies(response: Response) -> None:
    # 심을 때와 같은 path 로 지워야 실제로 지워진다.
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE_NAME, path=CSRF_COOKIE_PATH)


def _safe_return_to(value: str | None) -> str:
    """열린 리다이렉트 방지. 우리 사이트 안의 경로만 허용한다.

    '//evil.com' 은 프로토콜 상대 URL 이라 브라우저가 외부로 해석한다.
    '/\\evil.com' 도 일부 브라우저가 같게 본다. 둘 다 막는다.
    """
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    return value


@router.get("/google/start", summary="구글 로그인 시작")
def google_start(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    return_to: Annotated[str | None, Query(description="로그인 후 돌아갈 프론트 경로")] = None,
) -> RedirectResponse:
    started = service.start_login(redis_client, return_to=_safe_return_to(return_to))

    response = RedirectResponse(started.authorization_url, status_code=status.HTTP_302_FOUND)
    # state 를 훔쳐도 이 쿠키가 없으면 콜백을 완성할 수 없다.
    response.set_cookie(
        BROWSER_COOKIE_NAME,
        started.browser_token,
        max_age=BROWSER_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/google/callback", summary="구글 로그인 콜백")
def google_callback(
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    oauth_browser: Annotated[str | None, Cookie(alias=BROWSER_COOKIE_NAME)] = None,
) -> RedirectResponse:
    """구글이 돌아오는 지점. 토큰을 URL 에 절대 넣지 않는다.

    토큰을 쿼리스트링에 실으면 브라우저 기록·리퍼러·서버 로그에 그대로 남는다.
    쿠키만 심고 프론트로 보낸 뒤, 프론트가 refresh 를 호출해 Access 를 받아간다.
    """
    issued = service.complete_login(
        db, redis_client, code=code, state=state, browser_token=oauth_browser
    )

    base = settings.frontend_base_url.rstrip("/") + "/"
    destination = urljoin(base, issued.return_to.lstrip("/"))
    response = RedirectResponse(destination, status_code=status.HTTP_302_FOUND)
    _set_session_cookies(response, issued)
    response.delete_cookie(BROWSER_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return response


@router.post("/refresh", response_model=TokenResponse, summary="액세스 토큰 발급")
def refresh(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
    _csrf: None = CsrfProtected,
) -> TokenResponse:
    """Access 를 얻는 유일한 경로. 호출할 때마다 Refresh 도 새것으로 바뀐다."""
    issued = service.rotate(db, refresh_token)
    _set_session_cookies(response, issued)
    return TokenResponse(
        access_token=issued.access_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", response_model=LogoutResponse, summary="로그아웃")
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
    _csrf: None = CsrfProtected,
) -> LogoutResponse:
    """이미 발급된 Access 는 최대 15분 살아 있다. 즉시 무효화는 이번 범위가 아니다."""
    service.logout(db, refresh_token)
    _clear_session_cookies(response)
    return LogoutResponse()
