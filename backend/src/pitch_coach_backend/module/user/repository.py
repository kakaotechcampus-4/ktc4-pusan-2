"""User 테이블 쿼리. 커밋은 하지 않는다 (트랜잭션 경계는 service 의 몫)."""

import uuid

from sqlalchemy import func, select, update
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


def delete(db: Session, user: User) -> None:
    db.delete(user)
    db.flush()


def has_tutorial_report(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(User.tutorial_report_at).where(User.id == user_id)) is not None


def mark_tutorial_report(db: Session, user_id: uuid.UUID) -> None:
    # 처음 받은 시각을 남긴다
    db.execute(
        update(User)
        .where(User.id == user_id, User.tutorial_report_at.is_(None))
        .values(tutorial_report_at=func.now())
    )
