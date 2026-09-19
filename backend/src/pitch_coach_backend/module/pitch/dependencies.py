from select import select
import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from pitch_coach_backend.module.take.entity import Take
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.exception import NonExistentPitch, NonExistentTake
from pitch_coach_backend.module.pitch.repository import PitchRepository


def get_owned_pitch(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pitch_id: uuid.UUID,
) -> uuid.UUID:
    pitch = PitchRepository(db).get_owned(pitch_id, current_user.id)
    if pitch is None:
        raise NonExistentPitch()
    return pitch.id


OwnedPitch = Annotated[uuid.UUID, Depends(get_owned_pitch)]

def get_in_pitch(take_id: uuid.UUID, pitch_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> Take | None:
    take = db.scalar(
        select(Take).where(Take.id == take_id, Take.pitch_id == pitch_id)
    )

    if take is None:
        raise NonExistentTake()

    return take.id

BelongToPitch = Annotated[Take, Depends(get_in_pitch)]
