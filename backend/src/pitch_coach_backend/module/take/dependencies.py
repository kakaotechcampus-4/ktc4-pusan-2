import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository


def get_owned_take(
    db: Annotated[Session, Depends(get_db)],
    pitch_id: OwnedPitch,
    take_id: uuid.UUID,
) -> uuid.UUID:
    owned = TakeRepository(db).take_id_in_pitch(pitch_id, take_id)
    db.commit()
    if owned is None:
        raise NonExistentTake()

    return owned


OwnedTake = Annotated[uuid.UUID, Depends(get_owned_take)]
