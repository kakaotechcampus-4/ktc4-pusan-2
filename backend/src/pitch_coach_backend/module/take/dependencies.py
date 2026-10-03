from typing import Annotated
import uuid
from fastapi import Depends
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from sqlalchemy import select
from sqlalchemy.orm import Session
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.entity import Take
    
def get_owned_take(
    db: Annotated[Session, Depends(get_db)],
    pitch_id: OwnedPitch,
    take_id: uuid.UUID,
) -> uuid.UUID:
    take = db.scalar(
        select(Take).where(Take.id == take_id, Take.pitch_id == pitch_id)
    )

    if take is None:
        raise NonExistentTake()

    return take.id

OwnedTake = Annotated[uuid.UUID, Depends(get_owned_take)]