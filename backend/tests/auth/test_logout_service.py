"""auth service: 만료 · 세션 끝 · 탈퇴 시 토큰 · 로그아웃."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import repository, service
from pitch_coach_backend.module.auth.entity import RefreshToken
from pitch_coach_backend.module.auth.exception import InvalidRefreshToken
from tests.auth.conftest import login

# --- 만료 · 세션의 끝 ---------------------------------------------------


def test_expired_refresh_token_is_rejected(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    stored = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, issued.refresh_token)

    db_session.refresh(stored)
    assert stored.revoked_at is not None


# --- 회전 응답을 못 받은 재시도 --------------------------------------------


def _stored(db: Session, refresh_token: str) -> RefreshToken:
    stored = repository.get_refresh_token(db, security.hash_refresh_token(refresh_token))
    assert stored is not None
    return stored


# --- 만료 · 세션의 끝 ---------------------------------------------------


def test_login_sets_the_end_of_the_whole_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    stored = _stored(db_session, issued.refresh_token)

    expected = datetime.now(UTC) + timedelta(days=settings.refresh_session_max_days)
    assert abs((stored.session_expires_at - expected).total_seconds()) < 5
    assert stored.expires_at <= stored.session_expires_at


def test_rotation_never_extends_past_the_end_of_the_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """로그인한 지 오래돼 세션의 끝이 하루 남았다. 회전해도 14일이 아니라 그때까지만 산다."""
    issued = login(db_session, redis_client)
    session_end = datetime.now(UTC) + timedelta(days=1)
    stored = _stored(db_session, issued.refresh_token)
    stored.session_expires_at = session_end
    db_session.commit()

    rotated = _stored(db_session, service.rotate(db_session, issued.refresh_token).refresh_token)

    assert rotated.session_expires_at == session_end
    assert rotated.expires_at == session_end


def test_deleting_a_user_kills_their_refresh_tokens(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """탈퇴하면 남은 Refresh 로 다시 들어올 수 없다.

    FK 의 ON DELETE CASCADE 가 토큰 행을 지운다. 그래서 "사용자는 없는데 토큰은 살아 있는"
    상태 자체가 DB 에 존재할 수 없다 (service 의 user is None 분기는 그 뒤를 받치는 방어선).
    """
    issued = login(db_session, redis_client)

    db_session.delete(issued.user)
    db_session.commit()

    assert db_session.scalars(select(RefreshToken)).all() == []
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, issued.refresh_token)


# --- 로그아웃 -----------------------------------------------------------


def test_logout_revokes_the_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    service.logout(db_session, issued.refresh_token)

    # 폐기된 토큰이 다시 오면 재사용 탐지에 걸린다. 사용자에게는 같은 401 이다.
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, issued.refresh_token)


def test_logout_revokes_rotated_tokens_of_the_same_device(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    current = service.rotate(db_session, issued.refresh_token)

    service.logout(db_session, current.refresh_token)

    alive = db_session.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
    assert alive == []


def test_logout_leaves_other_devices_alone(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    laptop = login(db_session, redis_client)
    phone = login(db_session, redis_client)

    service.logout(db_session, laptop.refresh_token)

    assert service.rotate(db_session, phone.refresh_token) is not None


def test_logout_is_idempotent_and_silent(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """토큰 존재 여부를 알려주지 않는다. 두 번 호출해도 오류가 없다."""
    issued = login(db_session, redis_client)

    service.logout(db_session, issued.refresh_token)
    service.logout(db_session, issued.refresh_token)
    service.logout(db_session, None)
    service.logout(db_session, security.create_refresh_token())
