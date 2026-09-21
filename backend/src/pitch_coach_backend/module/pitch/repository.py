import uuid

from pitch_coach_backend.module.take.entity import Take, TakeSummary
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion


class PitchRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, pitch_id: uuid.UUID) -> Pitch | None:
        return self.db.get(Pitch, pitch_id)

    def get_owned(self, pitch_id: uuid.UUID, user_id: uuid.UUID) -> Pitch | None:
        return self.db.scalar(
            select(Pitch).where(Pitch.id == pitch_id, Pitch.user_id == user_id)
        )

    def get_all_by_user(self, user_id: uuid.UUID) -> list[Pitch]:
        return self.db.execute(
            select(Pitch).where(Pitch.user_id == user_id)
        ).scalars().all()

    def get_takes_with_scores_in_pitch(self, pitch_id: uuid.UUID) -> list[tuple[Take, int | None]]:
        return self.db.execute(
            select(Take, TakeSummary.score)
            .outerjoin(TakeSummary, TakeSummary.take_id == Take.id)
            .where(Take.pitch_id == pitch_id)
            # take_number 순서 - 먼저 한 순서대로 정렬됨.
            .order_by(Take.take_number)
        ).all()

    def save(self, pitch: Pitch) -> Pitch:
        self.db.add(pitch)
        self.db.flush()
        return pitch

    def delete(self, pitch: Pitch) -> None:
        self.db.delete(pitch)
        self.db.flush()

    def clear_best_take(self, pitch_id: uuid.UUID, take_id: uuid.UUID) -> None:
        self.db.execute(
            update(Pitch)
            .where(Pitch.id == pitch_id, Pitch.best_take_id == take_id)
            .values(best_take_id=None)
        )

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
