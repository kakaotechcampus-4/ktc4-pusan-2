"""토큰 발급·검증. 사용자 조회는 하지 않는다 (그건 auth/dependencies.py 의 몫).

Access 토큰과 Refresh 토큰은 형식부터 다르다.

- Access: JWT. 서명만으로 검증하므로 DB 를 보지 않는다. 대신 폐기할 수 없어 15분으로 짧다.
- Refresh: JWT 가 아니라 난수. 만료·폐기 여부를 DB 로 판단해야 즉시 무효화할 수 있다.
  DB 에는 해시만 저장하므로 DB 가 새도 토큰 원문은 복원되지 않는다.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException

# 환경마다 바뀌지 않으므로 설정이 아니라 상수
ALGORITHM = "HS256"

# 32바이트(=256비트) 난수. URL-safe base64 로 43자가 된다.
REFRESH_TOKEN_BYTES = 32


def create_access_token(subject: uuid.UUID | str, expires_minutes: int | None = None) -> str:
    now = datetime.now(UTC)
    if expires_minutes is None:
        expires_minutes = settings.access_token_expire_minutes
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        # 발급마다 다른 값. 지금은 쓰지 않지만 나중에 개별 토큰을 차단하려면
        # 이 값이 토큰 안에 이미 들어 있어야 한다 (사후 추가는 소급 적용되지 않는다).
        "jti": str(uuid.uuid7()),
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


def create_refresh_token() -> str:
    """예측 불가능한 난수 문자열. 이 값은 쿠키로만 나가고 DB 에 남지 않는다."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """DB 조회용 해시.

    비밀번호가 아니라 고엔트로피 난수라서 bcrypt 같은 느린 해시가 필요 없다.
    무차별 대입으로 원문을 찾을 수 없고, 매 요청 조회하므로 빠른 편이 낫다.
    salt 를 쓰지 않는 이유도 같다 — 해시로 바로 SELECT 해야 한다.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_matches(token: str, token_hash: str) -> bool:
    """해시 비교. 타이밍 공격을 피하려 compare_digest 를 쓴다."""
    return hmac.compare_digest(hash_refresh_token(token), token_hash)


def refresh_token_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
