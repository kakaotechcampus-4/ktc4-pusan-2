"""관리자 콘솔 인증.

브라우저 기본 로그인 창(HTTP Basic)으로 팀 공용 계정 하나를 확인한다. 확인은 여기 한 곳에서만 한다.
서버 로그(/admin/logs, Dozzle)도 Caddy 가 forward_auth 로 /admin/api/auth 에 물어 이 확인을 거친다.
"""

import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pitch_coach_backend.core.client_ip import client_ip
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.admin.exception import AdminActionForbidden, AdminConsoleDisabled

logger = logging.getLogger(__name__)

# 브라우저는 이 이름으로 입력한 계정을 기억한다
REALM = "pitch-coach-admin"
# 상태를 바꾸는 관리자 요청에만 요구하는 헤더. 콘솔 스크립트(static/app.js)가 붙인다
ACTION_HEADER = "X-Admin-Action"

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
    # X-Forwarded-Uri 에 있다. 입력한 아이디는 남기지 않는다 — 아이디 칸에 비밀번호를 잘못 넣으면
    # 그 값이 로그에 보인다
    if credentials is not None:
        logger.warning(
            "관리자 로그인 실패 ip=%s path=%s",
            client_ip(request),
            request.headers.get("x-forwarded-uri", request.url.path),
        )
    # AppException 은 WWW-Authenticate 를 Bearer 로 채운다.
    # Basic 이어야 브라우저가 로그인 창을 띄운다
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
    )


def require_admin_action(
    action: Annotated[str | None, Header(alias=ACTION_HEADER)] = None,
) -> None:
    """상태를 바꾸는 관리자 요청(POST)의 CSRF 방어. 로그인 확인(`require_admin`) 뒤에 건다.

    Basic 로그인은 브라우저가 기억해 두었다가 다른 사이트가 이 주소로 보낸 요청에도 붙인다. 그래서
    로그인만으로는 콘솔에서 누른 요청인지 알 수 없다. 다른 사이트의 form 은 헤더를 붙이지 못하고,
    다른 출처의 fetch 는 이 헤더 때문에 preflight 를 거치는데 CORS 설정(main.py)이 허용하지 않는다.
    """
    if action != "1":
        raise AdminActionForbidden()
