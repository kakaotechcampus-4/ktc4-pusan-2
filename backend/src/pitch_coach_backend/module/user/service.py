"""User 비즈니스 로직.

다른 도메인(auth)은 이 service 만 호출한다. repository·entity 를 직접 import 하지 않는다.
"""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.user import repository
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.module.user.exception import UserNotFound


def get(db: Session, user_id: uuid.UUID) -> User:
    user = repository.get_by_id(db, user_id)
    if user is None:
        raise UserNotFound()
    return user


def find(db: Session, user_id: uuid.UUID) -> User | None:
    """없을 수 있는 조회. 토큰의 sub 가 이미 지워진 사용자를 가리킬 때 쓴다."""
    return repository.get_by_id(db, user_id)


def find_by_email(db: Session, email: str) -> User | None:
    return repository.get_by_email(db, email)


def create(db: Session, *, email: str, name: str) -> User:
    """커밋하지 않는다. 소셜 계정 생성과 한 트랜잭션으로 묶여야 하기 때문."""
    return repository.create(db, email=email, name=name)
