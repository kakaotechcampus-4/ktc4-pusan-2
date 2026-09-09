"""JWT 발급·검증. 사용자 조회는 하지 않는다 (그건 auth/dependencies.py 의 몫)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException

# 환경마다 바뀌지 않으므로 설정이 아니라 상수
ALGORITHM = "HS256"


def create_access_token(subject: uuid.UUID | str, expires_minutes: int | None = None) -> str:
    now = datetime.now(UTC)
    if expires_minutes is None:
        expires_minutes = settings.access_token_expire_minutes
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """만료·위조·형식 오류는 모두 UnauthorizedException 으로 변환한다."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedException("토큰이 만료되었습니다.") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedException("유효하지 않은 토큰입니다.") from e

    if payload.get("type") != "access":
        raise UnauthorizedException("액세스 토큰이 아닙니다.")
    return payload
