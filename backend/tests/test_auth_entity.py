"""OAuthAccount·RefreshToken 의 DB 제약이 실제로 걸리는지 확인한다.

이 제약들이 동시 최초 로그인 경쟁과 토큰 재사용을 막는 마지막 방어선이라
애플리케이션 코드가 아니라 DB 수준에서 확인한다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.auth.entity import OAuthAccount, RefreshToken
from pitch_coach_backend.module.user.entity import User


def _user(db: Session, email: str) -> User:
    user = User(email=email, name="테스터")
    db.add(user)
    db.commit()
    return user


def _refresh(user_id: uuid.UUID, token_hash: str, **kw: object) -> RefreshToken:
    return RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        device_id=kw.pop("device_id", uuid.uuid7()),
        expires_at=kw.pop("expires_at", datetime.now(UTC) + timedelta(days=14)),
        **kw,
    )


def test_oauth_account_is_one_to_one_with_user(db_session: Session) -> None:
    user = _user(db_session, "one@example.com")
    db_session.add(OAuthAccount(user_id=user.id, provider="google", provider_subject="sub-1"))
    db_session.commit()

    # 같은 사용자에게 두 번째 소셜 계정을 붙일 수 없다 (user_id UNIQUE)
    db_session.add(OAuthAccount(user_id=user.id, provider="google", provider_subject="sub-2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_provider_subject_cannot_map_to_two_users(db_session: Session) -> None:
    """동시 최초 로그인이 경쟁해도 sub 하나에 계정 하나만 생긴다."""
    first = _user(db_session, "first@example.com")
    second = _user(db_session, "second@example.com")
    db_session.add(OAuthAccount(user_id=first.id, provider="google", provider_subject="same-sub"))
    db_session.commit()

    db_session.add(OAuthAccount(user_id=second.id, provider="google", provider_subject="same-sub"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_user_cascades_to_oauth_account(db_session: Session) -> None:
    """탈퇴하면 연결된 소셜 계정도 사라진다."""
    user = _user(db_session, "bye@example.com")
    db_session.add(OAuthAccount(user_id=user.id, provider="google", provider_subject="sub-bye"))
    db_session.commit()

    db_session.delete(user)
    db_session.commit()

    assert db_session.scalars(select(OAuthAccount)).all() == []


def test_refresh_token_hash_is_unique(db_session: Session) -> None:
    user = _user(db_session, "token@example.com")
    db_session.add(_refresh(user.id, "hash-1"))
    db_session.commit()

    db_session.add(_refresh(user.id, "hash-1"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_refresh_token_defaults_to_not_revoked(db_session: Session) -> None:
    user = _user(db_session, "alive@example.com")
    token = _refresh(user.id, "hash-alive")
    db_session.add(token)
    db_session.commit()
    db_session.refresh(token)

    assert token.revoked_at is None
    assert token.id.version == 7


def test_one_device_can_hold_many_rotated_tokens(db_session: Session) -> None:
    """회전하면 같은 device_id 로 새 행이 쌓인다. 재사용 탐지 때 통째로 폐기하려는 구조다."""
    user = _user(db_session, "rotate@example.com")
    device_id = uuid.uuid7()
    db_session.add(_refresh(user.id, "hash-old", device_id=device_id))
    db_session.add(_refresh(user.id, "hash-new", device_id=device_id))
    db_session.commit()

    rows = db_session.scalars(select(RefreshToken).where(RefreshToken.device_id == device_id)).all()
    assert len(rows) == 2
