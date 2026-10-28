"""관리자 콘솔 인증.

브라우저 기본 로그인 창(HTTP Basic)으로 팀 공용 계정 하나를 확인한다. 확인은 여기 한 곳에서만 한다.
서버 로그(/admin/logs, Dozzle)도 Caddy 가 forward_auth 로 /admin/api/auth 에 물어 이 확인을 거친다.
"""

import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.admin.exception import AdminConsoleDisabled

logger = logging.getLogger(__name__)

# 브라우저는 이 이름으로 입력한 계정을 기억한다
REALM = "pitch-coach-admin"

# 계정이 없거나 틀리면 401 과 함께 Basic 을 알려야 해서 직접 판단한다 (auto_error=False).
# 헤더가 깨져 있으면 auto_error 와 상관없이 HTTPBasic 이 401 을 낸다
_basic = HTTPBasic(realm=REALM, auto_error=False)


def require_console_enabled() -> None:
    if not (settings.admin_username and settings.admin_password):
        raise AdminConsoleDisabled()


def _matches(credentials: HTTPBasicCredentials) -> bool:
    # 둘 다 비교한 뒤 합친다. 아이디가 틀렸을 때 일찍 끝나면 응답 시간으로 아이디를 알아낼 수 있다
    username_ok = secrets.compare_digest(
        credentials.username.encode(), settings.admin_username.encode()
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode(), settings.admin_password.encode()
    )
    return username_ok and password_ok


def require_admin(
    request: Request,
    # 앞에 둔 의존성이 먼저 풀린다. 꺼진 콘솔은 로그인 헤더를 보기 전에 404 로 끝낸다
    _: Annotated[None, Depends(require_console_enabled)],
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> None:
    if credentials is not None and _matches(credentials):
        return
    # 계정 없이 온 요청은 남기지 않는다. 브라우저는 처음에 계정 없이 와서 401 을 받고 창을 띄운다.
    # BE 는 Caddy 뒤라 보낸 곳은 X-Forwarded-For 에, Caddy 가 물을 때(forward_auth)의 원래 주소는
    # X-Forwarded-Uri 에 있다
    if credentials is not None:
        logger.warning(
            "관리자 로그인 실패 username=%r ip=%s path=%s",
            credentials.username,
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-forwarded-uri", request.url.path),
        )
    # AppException 은 WWW-Authenticate 를 Bearer 로 채운다.
    # Basic 이어야 브라우저가 로그인 창을 띄운다
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
    )
