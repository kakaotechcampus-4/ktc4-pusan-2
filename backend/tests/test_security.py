import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import (
    ALGORITHM,
    create_access_token,
    decode_access_token,
)


def test_roundtrip() -> None:
    user_id = uuid.uuid7()
    payload = decode_access_token(create_access_token(user_id))
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


def test_expired_token_rejected() -> None:
    token = create_access_token(uuid.uuid7(), expires_minutes=-1)
    with pytest.raises(UnauthorizedException, match="만료"):
        decode_access_token(token)


def test_tampered_token_rejected() -> None:
    token = create_access_token(uuid.uuid7())
    with pytest.raises(UnauthorizedException):
        decode_access_token(token[:-4] + "abcd")


def test_garbage_rejected() -> None:
    with pytest.raises(UnauthorizedException):
        decode_access_token("not.a.jwt")


def test_non_access_type_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "x", "iat": now, "exp": now + timedelta(minutes=5), "type": "refresh"},
        settings.jwt_secret_key,
        algorithm=ALGORITHM,
    )
    with pytest.raises(UnauthorizedException, match="액세스"):
        decode_access_token(token)


def test_missing_sub_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"exp": now + timedelta(minutes=5), "type": "access"},
        settings.jwt_secret_key,
        algorithm=ALGORITHM,
    )
    with pytest.raises(UnauthorizedException):
        decode_access_token(token)
