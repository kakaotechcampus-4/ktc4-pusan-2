from ast import List
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Calibration, Mission, Take, TakeSummary


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_takes_with_scores_in_pitch(self, pitch_id: uuid.UUID) -> list[Take] | None:
        return self.db.execute(
            select(Take).where(Take.pitch_id == pitch_id)
        ).scalars().all()

    def get_scores_in_take(self, take_id: List[uuid.UUID]) -> int | None:
        return self.db.execute(
            select(TakeSummary.score).where(TakeSummary.take_id.in_(take_id))
        ).scalars().all()
    
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

    def next_take_number(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(Take.take_number)).where(Take.pitch_id == pitch_id)
        )
        return (current or 0) + 1

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
    
    def last_transcript_cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        """저장된 마지막 (seq, stt_session_no). 하나도 없으면 (0, 0).

        WebSocket 스트림이 새로 만들어질 때 번호를 이어 받기 위한 값이다.
        """
        row = self.db.execute(
            select(
                func.coalesce(func.max(TakeTranscriptSegment.seq), 0),
                func.coalesce(func.max(TakeTranscriptSegment.stt_session_no), 0),
            ).where(TakeTranscriptSegment.take_id == take_id)
        ).one()
        return int(row[0]), int(row[1])

    def save_transcript_segment(self, segment: TakeTranscriptSegment) -> TakeTranscriptSegment:
        self.db.add(segment)
        self.db.flush()
        return segment
