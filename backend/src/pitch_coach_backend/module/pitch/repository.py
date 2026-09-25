import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion, Standards


class PitchRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, pitch_id: uuid.UUID) -> Pitch | None:
        return self.db.get(Pitch, pitch_id)

    def get_owned(self, pitch_id: uuid.UUID, user_id: uuid.UUID) -> Pitch | None:
        return self.db.scalar(
            select(Pitch).where(Pitch.id == pitch_id, Pitch.user_id == user_id)
        )

    def save(self, pitch: Pitch) -> Pitch:
        self.db.add(pitch)
        self.db.flush()
        return pitch

    def delete(self, pitch: Pitch) -> None:
        self.db.delete(pitch)
        self.db.flush()

    def next_presentation_version(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(PresentationVersion.version)).where(
                PresentationVersion.pitch_id == pitch_id
            )
        )
        return (current or 0) + 1

    def save_presentation(self, presentation_version: PresentationVersion) -> PresentationVersion:
        self.db.add(presentation_version)
        self.db.flush()
        return presentation_version

    def next_script_version(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(ScriptVersion.version)).where(ScriptVersion.pitch_id == pitch_id)
        )
        return (current or 0) + 1

    def save_script(self, script_version: ScriptVersion) -> ScriptVersion:
        self.db.add(script_version)
        self.db.flush()
        return script_version

    def get_presentations(self, pitch_id: uuid.UUID) -> list[PresentationVersion]:
        return self.db.scalars(
            select(PresentationVersion).where(PresentationVersion.pitch_id == pitch_id)
        ).all()

    def get_scripts(self, pitch_id: uuid.UUID) -> list[ScriptVersion]:
        return self.db.scalars(
            select(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)
        ).all()

    def get_evaluations(self, pitch_id: uuid.UUID) -> list[Standards]:
        return self.db.scalars(
            select(Standards).where(Standards.pitch_id == pitch_id)
        ).all()

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