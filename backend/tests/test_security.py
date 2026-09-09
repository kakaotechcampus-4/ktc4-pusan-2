import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_refresh_token,
    refresh_token_expires_at,
    refresh_token_matches,
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


def test_access_token_carries_unique_jti() -> None:
    """개별 토큰 차단을 나중에 붙이려면 jti 가 지금부터 토큰 안에 있어야 한다."""
    user_id = uuid.uuid7()
    first = decode_access_token(create_access_token(user_id))
    second = decode_access_token(create_access_token(user_id))

    assert first["jti"] != second["jti"]


def test_access_token_expires_in_fifteen_minutes_by_default() -> None:
    payload = decode_access_token(create_access_token(uuid.uuid7()))
    lifetime = payload["exp"] - payload["iat"]

    assert lifetime == 15 * 60


def test_refresh_tokens_are_unpredictable() -> None:
    tokens = {create_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    # token_urlsafe(32) 는 43자. refresh_tokens.token_hash 는 해시라 길이와 무관하다.
    assert all(len(t) >= 43 for t in tokens)


def test_refresh_token_hash_is_stable_and_fits_the_column() -> None:
    token = create_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    # entity 의 String(64) 와 맞아야 한다
    assert len(hash_refresh_token(token)) == 64


def test_different_refresh_tokens_hash_differently() -> None:
    assert hash_refresh_token(create_refresh_token()) != hash_refresh_token(create_refresh_token())


def test_refresh_token_matches_only_its_own_hash() -> None:
    token = create_refresh_token()
    other = create_refresh_token()

    assert refresh_token_matches(token, hash_refresh_token(token))
    assert not refresh_token_matches(other, hash_refresh_token(token))


def test_refresh_token_expiry_follows_settings() -> None:
    expected = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    actual = refresh_token_expires_at()

    assert abs((actual - expected).total_seconds()) < 5
