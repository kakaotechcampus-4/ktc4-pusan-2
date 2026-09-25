import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeSummary,
    TakeTranscriptSegment,
)


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_takes_with_scores_in_pitch(self, pitch_id: uuid.UUID) -> list[Take]:
        return self.db.execute(
            select(Take).where(Take.pitch_id == pitch_id)
        ).scalars().all()

    def get_scores_in_take(self, take_id: list[uuid.UUID]) -> list[int]:
        return self.db.execute(
            select(TakeSummary.score).where(TakeSummary.take_id.in_(take_id))
        ).scalars().all()

    def get_owned(self, take_id: uuid.UUID, user_id: uuid.UUID) -> Take | None:
        """사용자의 pitch 에 속한 take. 없거나 남의 것이면 None — 둘을 구분하지 않는다."""
        return self.db.scalar(
            select(Take)
            .join(Pitch, Pitch.id == Take.pitch_id)
            .where(Take.id == take_id, Pitch.user_id == user_id)
        )

    def get_max_score_take_in_pitch(self, pitch_id: uuid.UUID) -> uuid.UUID | None:
        return self.db.scalar(
            select(TakeSummary.take_id)
            .join(Take, TakeSummary.take_id == Take.id)
            .where(Take.pitch_id == pitch_id)
            .order_by(TakeSummary.score.desc())
            .limit(1)
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
