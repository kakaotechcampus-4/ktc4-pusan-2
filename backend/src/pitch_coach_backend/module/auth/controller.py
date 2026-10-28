"""인증 엔드포인트. HTTP 관심사(쿠키·리다이렉트·헤더)만 다루고 로직은 service 에 맡긴다.

엔드포인트는 네 개뿐이다. Access JWT 를 얻는 경로는 refresh 하나로 통일한다.

    GET  /api/auth/google/start     구글 인증 화면으로 보낸다
    GET  /api/auth/google/callback  구글이 돌아오는 곳. 쿠키를 심고 프론트로 보낸다
    POST /api/auth/refresh          쿠키의 Refresh 로 Access 를 발급 (+ Refresh 회전)
    POST /api/auth/logout           현재 세션의 Refresh 폐기

쿠키가 셋이다.
    refresh_token   HttpOnly. Path=/api/auth — 다른 API 요청에는 실려 나가지 않는다
    csrf_token      JS 가 읽어 헤더에 실어야 하므로 HttpOnly 가 아니다
    oauth_browser_* start~callback 동안만 사는 임시 쿠키. 로그인 요청마다 이름 끝에 state 를 붙인다
"""

import logging
import re
import secrets
from typing import Annotated
from urllib.parse import urlencode, urljoin

import redis
from fastapi import APIRouter, Cookie, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.core.exceptions import AppException
from pitch_coach_backend.core.redis import get_redis
from pitch_coach_backend.module.auth import service, state_store
from pitch_coach_backend.module.auth.dependencies import (
    CSRF_COOKIE_NAME,
    CsrfProtected,
)
from pitch_coach_backend.module.auth.dto import LogoutResponse, TokenResponse
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    GoogleIdentityRejected,
    GoogleTokenExchangeFailed,
    InvalidAuthorizationRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)

REFRESH_COOKIE_NAME = "refresh_token"
BROWSER_COOKIE_NAME = "oauth_browser"

# Refresh 쿠키는 인증 엔드포인트에만 실린다. /api/pitches 같은 요청에 함께 나가지 않아
# 유출 표면이 줄어든다. csrf_token 은 프론트(/)에서 읽어야 해서 Path=/ 다.
REFRESH_COOKIE_PATH = "/api/auth"
CSRF_COOKIE_PATH = "/"

# 로그인 시작부터 콜백까지만 살면 된다. state 의 TTL 과 맞춘다.
BROWSER_COOKIE_MAX_AGE = 600

# 우리가 만드는 state 의 모양(token_urlsafe). 쿠키 이름에 넣어도 되는 글자뿐이다.
_STATE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def browser_cookie_name(state: str) -> str:
    """로그인 요청마다 따로 심는 임시 쿠키의 이름.

    이름이 하나면 두 탭에서 로그인을 시작했을 때 나중 탭의 쿠키가 먼저 탭의 것을 덮어써
    먼저 탭의 콜백이 실패한다.
    """
    return f"{BROWSER_COOKIE_NAME}_{state}"


def _delete_browser_cookie(response: Response, state: str | None) -> None:
    # 콜백의 state 는 밖에서 온 값이다. 쿠키 이름에 못 쓰는 글자가 섞이면 Set-Cookie 를
    # 만들다 500 이 나므로, 우리가 만든 모양일 때만 지운다
    if state and _STATE.match(state):
        response.delete_cookie(browser_cookie_name(state), path=REFRESH_COOKIE_PATH)


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


def clear_session_cookies(response: Response) -> None:
    """로그아웃과 회원 탈퇴(/users/me)가 쓴다."""
    # 심을 때와 같은 path 로 지워야 실제로 지워진다.
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE_NAME, path=CSRF_COOKIE_PATH)


# 구글이 돌려주는 오류 코드는 소문자와 밑줄뿐이다. 그 밖의 값은 URL 에 싣지 않는다.
_ERROR_CODE = re.compile(r"\A[a-z_]{1,40}\Z")

# 콜백 처리 중 난 도메인 예외 -> 프론트로 넘기는 auth_error. 구글 오류 코드와 같은 모양이다.
# 프론트는 access_denied 외에는 같은 문구를 보여주므로 원인 추적용이다.
# 표에 없는 예외(DB 오류 등)는 login_failed 다
_CALLBACK_ERROR_CODES: dict[type[Exception], str] = {
    InvalidAuthorizationRequest: "invalid_request",
    GoogleTokenExchangeFailed: "token_exchange_failed",
    GoogleIdentityRejected: "identity_rejected",
    EmailAlreadyRegistered: "email_already_registered",
}


def _callback_error_code(exc: Exception) -> str:
    return _CALLBACK_ERROR_CODES.get(type(exc), "login_failed")


def _safe_return_to(value: str | None) -> str:
    """열린 리다이렉트 방지. 우리 사이트 안의 경로만 허용한다.

    막아야 하는 형태가 셋이다.

    - '//evil.com'        프로토콜 상대 URL. 브라우저가 외부로 해석한다
    - '/\\evil.com'        일부 브라우저가 위와 같게 본다
    - '/https://evil.com' 앞의 '/' 를 떼면 절대 URL 이 된다. urljoin 이 그대로 승격시킨다

    셋째는 실제로 뚫렸던 경로다. 여기서 막고 _frontend_url 에서 한 번 더 확인한다.
    """
    if not value or not value.startswith("/"):
        return "/"
    if value.startswith(("//", "/\\")):
        return "/"
    if "://" in value or "\\" in value:
        return "/"
    return value


def _frontend_url(return_to: str | None, *, auth_error: str | None = None) -> str:
    """프론트엔드 안의 주소만 만든다. 밖으로 나가는 값이 나오면 루트로 되돌린다."""
    base = settings.frontend_base_url.rstrip("/") + "/"
    # lstrip('/') 를 하면 '/https://evil.com' 이 절대 URL 로 승격된다. 하면 안 된다.
    destination = urljoin(base, _safe_return_to(return_to))

    # 위 검사를 빠져나간 형태가 있어도 여기서 걸린다 (2차 방어).
    if not destination.startswith(base):
        destination = base

    if auth_error:
        code = auth_error if _ERROR_CODE.match(auth_error) else "login_failed"
        joiner = "&" if "?" in destination else "?"
        destination += joiner + urlencode({"auth_error": code})
    return destination


@router.get("/google/start", summary="구글 로그인 시작")
def google_start(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    return_to: Annotated[str | None, Query(description="로그인 후 돌아갈 프론트 경로")] = None,
) -> RedirectResponse:
    try:
        started = service.start_login(redis_client, return_to=_safe_return_to(return_to))
    except Exception:
        # 로그인 버튼이 화면째 이 주소로 옮겨 온다. Redis 오류 등으로 500 JSON 을 주면
        # 사용자가 그대로 보므로 콜백 실패와 같이 프론트로 돌려보낸다
        logger.exception("구글 로그인 시작 중 예상하지 못한 오류")
        return RedirectResponse(
            _frontend_url("/", auth_error="login_failed"), status_code=status.HTTP_302_FOUND
        )

    response = RedirectResponse(started.authorization_url, status_code=status.HTTP_302_FOUND)
    # state 를 훔쳐도 이 쿠키가 없으면 콜백을 완성할 수 없다.
    response.set_cookie(
        browser_cookie_name(started.state),
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
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """구글이 돌아오는 지점. 토큰을 URL 에 절대 넣지 않는다.

    토큰을 쿼리스트링에 실으면 브라우저 기록·리퍼러·서버 로그에 그대로 남는다.
    쿠키만 심고 프론트로 보낸 뒤, 프론트가 refresh 를 호출해 Access 를 받아간다.

    code 와 state 를 필수로 선언하지 않는 이유: 사용자가 동의 화면에서 취소하면
    구글은 code 없이 `?error=access_denied&state=...` 로 돌려보낸다. 필수로 두면
    사용자가 원인 모를 422 JSON 을 보게 된다. 프론트로 돌려보내는 편이 맞다.
    """
    if code is None or state is None:
        if state:
            # 남은 인증 요청을 Redis 에서 지운다. TTL 로도 사라지지만 즉시 정리한다.
            state_store.consume(redis_client, state)
        response = RedirectResponse(
            _frontend_url("/", auth_error=error or "invalid_request"),
            status_code=status.HTTP_302_FOUND,
        )
        _delete_browser_cookie(response, state)
        return response

    try:
        issued = service.complete_login(
            db,
            redis_client,
            code=code,
            state=state,
            browser_token=request.cookies.get(browser_cookie_name(state)),
        )
    except Exception as e:
        # 브라우저가 최상위로 이동해 온 요청이라 JSON 을 돌려주면 사용자가 그대로 본다.
        # 취소했을 때와 같이 프론트로 돌려보내고 원인은 auth_error 코드로만 알린다.
        # 예상하지 못한 예외도 500 JSON 대신 같은 길로 보내고, 원인은 서버 로그에 남긴다
        if not isinstance(e, AppException):
            logger.exception("구글 로그인 콜백 처리 중 예상하지 못한 오류")
        response = RedirectResponse(
            _frontend_url("/", auth_error=_callback_error_code(e)),
            status_code=status.HTTP_302_FOUND,
        )
        _delete_browser_cookie(response, state)
        return response

    response = RedirectResponse(_frontend_url(issued.return_to), status_code=status.HTTP_302_FOUND)
    _set_session_cookies(response, issued)
    _delete_browser_cookie(response, state)
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
    clear_session_cookies(response)
    return LogoutResponse()
