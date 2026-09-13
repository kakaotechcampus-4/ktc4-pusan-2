import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.user.entity import User


def test_create_user_fills_defaults(db_session: Session) -> None:
    user = User(email="a@example.com", name="A")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert isinstance(user.id, uuid.UUID)
    assert user.id.version == 7
    assert user.created_at is not None
    assert user.updated_at is not None
    assert db_session.scalar(select(User).where(User.email == "a@example.com")) is user


def test_email_unique(db_session: Session) -> None:
    db_session.add(User(email="dup@example.com", name="A"))
    db_session.commit()

    db_session.add(User(email="dup@example.com", name="B"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_each_test_starts_empty(db_session: Session) -> None:
    """앞 테스트가 commit 한 데이터가 롤백되어 보이지 않아야 한다."""
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
