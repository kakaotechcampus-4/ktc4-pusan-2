"""다른 도메인이 Take 쪽 값을 읽는 입구. pitch 서비스가 저장소를 직접 부르지 않게 한다."""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import TakeRepository


def latest_reviewed_take(db: Session, pitch_id: uuid.UUID) -> Take | None:
    return TakeRepository(db).get_latest_reviewed_take_in_pitch(pitch_id)


def latest_take(db: Session, pitch_id: uuid.UUID) -> Take | None:
    return TakeRepository(db).get_latest_take_in_pitch(pitch_id)
