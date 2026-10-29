"""User 비즈니스 로직.

다른 도메인(auth)은 이 service 만 호출한다. repository·entity 를 직접 import 하지 않는다.
"""

import uuid

from sqlalchemy import func
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


def update_settings(
    db: Session,
    user: User,
    *,
    raw_data_consent: bool | None,
    test_consent_version: int | None = None,
) -> User:
    """마이페이지 설정을 바꾸고 커밋한다. 원자료 사용 동의는 바꾼 뒤에 시작하는 Take 부터다 —
    Take 는 시작할 때 이 값을 복사해 그 값만 본다 (`docs/ai/architecture.md` 1-1).

    테스트 참여 동의는 버전이 바뀔 때만 시각을 새로 적는다. 같은 버전을 다시 보내면 처음 동의한
    시각을 그대로 둔다.
    """
    if raw_data_consent is not None:
        user.raw_data_consent = raw_data_consent
    if test_consent_version is not None and test_consent_version != user.test_consent_version:
        user.test_consent_version = test_consent_version
        # DB 시각으로 적는다. 커밋 뒤 다시 읽어 다른 시각 칸과 같은 형식으로 돌려준다
        user.test_consent_at = func.now()
    db.commit()
    return user


def delete(db: Session, user: User) -> None:
    """회원을 지운다. 되살릴 수 없다.

    소셜 계정·Refresh 토큰·발표와 그 아래 행은 FK의 CASCADE로 DB가 같이 지운다.
    Take별 설문·1차 배포 평점·의견은 user_id를 NULL로 바꿔 남긴다.
    같은 구글 계정으로 다시 로그인하면 기록이 없는 새 회원이 된다.
    """
    repository.delete(db, user)
    db.commit()


def exists(db: Session, user_id: uuid.UUID) -> bool:
    """인증 뒤 탈퇴한 경우도 확인하도록 캐시 없이 DB를 읽는다."""
    return repository.exists(db, user_id)
