import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
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
