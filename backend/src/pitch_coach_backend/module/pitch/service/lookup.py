"""다른 도메인이 발표 쪽 값을 읽는 입구. take 서비스가 저장소를 직접 부르지 않게 한다."""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.pitch.repository import PitchRepository


def owned_pitch(db: Session, pitch_id: uuid.UUID, user_id: uuid.UUID) -> Pitch | None:
    return PitchRepository(db).get_owned(pitch_id, user_id)


def pitch_by_id(db: Session, pitch_id: uuid.UUID) -> Pitch | None:
    return PitchRepository(db).get_by_id(pitch_id)


def script_in_pitch(
    db: Session, pitch_id: uuid.UUID, script_version_id: uuid.UUID
) -> ScriptVersion | None:
    return PitchRepository(db).get_script_in_pitch(pitch_id, script_version_id)


def count_slides(db: Session, script_version_id: uuid.UUID) -> int:
    return PitchRepository(db).count_slides(script_version_id)


def lock_for_new_version(db: Session, pitch_id: uuid.UUID) -> bool:
    return PitchRepository(db).lock_for_new_version(pitch_id)


def presentation_in_pitch(
    db: Session, pitch_id: uuid.UUID, presentation_version_id: uuid.UUID
) -> PresentationVersion | None:
    return PitchRepository(db).get_presentation_detail(pitch_id, presentation_version_id)
