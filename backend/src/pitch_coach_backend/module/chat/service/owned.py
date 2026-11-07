"""문자열 ID 읽기와 이 사용자의 피치 · Take 인지 보기.

AI 가 낸 ID · 저장해 둔 ID 는 문자열이라 읽지 못할 수 있다. 읽지 못한 ID 는 없는 것과 같게
다룬다. 소유 확인을 여기 한 곳에 두어 행동마다 권한 기준이 갈라지지 않게 한다.
"""

import uuid
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.pitch.repository import PitchRepository


def parse_uuid(value: Any) -> uuid.UUID | None:
    """UUID 로 읽는다. 읽을 수 없으면 None."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def owned_pitch(db: Session, user_id: uuid.UUID, pitch_id: Any) -> Pitch | None:
    """이 사용자의 피치. 없거나 남의 것이거나 ID 를 읽을 수 없으면 None."""
    parsed = parse_uuid(pitch_id)
    return PitchRepository(db).get_owned(parsed, user_id) if parsed else None


def owned_take(db: Session, user_id: uuid.UUID, take_id: Any) -> Row | None:
    """이 사용자 피치의 Take 를 리뷰 판과 함께 읽는다. 없거나 남의 것이거나 ID 를 읽을 수 없으면
    None."""
    parsed = parse_uuid(take_id)
    take = ChatRepository(db).get_take_with_review(parsed) if parsed else None
    if take is None or PitchRepository(db).get_owned(take.Take.pitch_id, user_id) is None:
        return None
    return take


def owned_take_in(db: Session, user_id: uuid.UUID, pitch_id: Any, take_id: Any) -> Row | None:
    """`owned_take` 에 더해 그 피치의 Take 여야 한다."""
    take = owned_take(db, user_id, take_id)
    if take is None or take.Take.pitch_id != parse_uuid(pitch_id):
        return None
    return take
