import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Calibration, Mission, Take


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_in_pitch(self, take_id: uuid.UUID, pitch_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take).where(Take.id == take_id, Take.pitch_id == pitch_id)
        )

    def get_presentation_version_in_pitch(
        self, presentation_version_id: uuid.UUID, pitch_id: uuid.UUID
    ) -> uuid.UUID | None:
        return self.db.scalar(
            select(PresentationVersion.id).where(
                PresentationVersion.id == presentation_version_id,
                PresentationVersion.pitch_id == pitch_id,
            )
        )

    def get_script_version_in_pitch(
        self, script_version_id: uuid.UUID, pitch_id: uuid.UUID
    ) -> uuid.UUID | None:
        return self.db.scalar(
            select(ScriptVersion.id).where(
                ScriptVersion.id == script_version_id,
                ScriptVersion.pitch_id == pitch_id,
            )
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

    def get_latest_take_in_pitch(self, pitch_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take)
            .where(Take.pitch_id == pitch_id)
            .order_by(Take.created_at.desc())
            .limit(1)
        )

    def get_missions_in_take(self, take_id: uuid.UUID) -> list[Mission] | None:
        return self.db.execute(
            select(Calibration.mission).where(Calibration.take_id == take_id)
        ).scalars().all()

    def save_missions(self, missions: list[Mission]) -> list[Mission]:
        self.db.add_all(missions)
        self.db.flush()
        return missions
