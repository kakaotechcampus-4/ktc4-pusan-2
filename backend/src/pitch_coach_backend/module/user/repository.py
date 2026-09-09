"""User 테이블 쿼리. 커밋은 하지 않는다 (트랜잭션 경계는 service 의 몫)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.user.entity import User


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def create(db: Session, *, email: str, name: str) -> User:
    user = User(email=email, name=name)
    db.add(user)
    # PK 와 타임스탬프를 이 자리에서 확정한다. 호출자가 user.id 를 바로 쓴다.
    db.flush()
    return user
