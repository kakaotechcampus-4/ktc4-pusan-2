import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Calibration, Take


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_in_pitch(self, take_id: uuid.UUID, pitch_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take).where(Take.id == take_id, Take.pitch_id == pitch_id)
        )

    def save(self, take: Take) -> Take:
        self.db.add(take)
        self.db.flush()
        return take

    def delete(self, take: Take) -> None:
        self.db.delete(take)
        self.db.flush()

    def save_calibration(self, calibration: Calibration) -> Calibration:
        self.db.add(calibration)
        self.db.flush()
        return calibration
