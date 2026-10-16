import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeAIResults,
    TakeSummary,
    TakeTranscriptSegment,
    TakeAIResult,
)


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_take(self, take_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take).where(Take.id == take_id)
        )

    def get_pitch_id_of_take(self, take_id: uuid.UUID) -> uuid.UUID | None:
        return self.db.scalar(
            select(Take.pitch_id).where(Take.id == take_id)
        )

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
        values = {
            column.key: getattr(calibration, column.key)
            for column in Calibration.__table__.columns
            if column.key not in ("id", "created_at")
        }
        stmt = insert(Calibration).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Calibration.take_id],
            set_={key: stmt.excluded[key] for key in values if key != "take_id"},
        ).returning(Calibration)
        return self.db.scalars(stmt, execution_options={"populate_existing": True}).one()

    def next_take_number(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(Take.take_number)).where(Take.pitch_id == pitch_id)
        )
        return (current or 0) + 1

    def get_latest_take_in_pitch(self, pitch_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take)
            .where(Take.pitch_id == pitch_id)
            # created_at 은 같은 트랜잭션 안에서 같아질 수 있다. 번호는 pitch 안에서 유일하다
            .order_by(Take.take_number.desc())
            .limit(1)
        )

    def get_previous_take_results(self, take_id: uuid.UUID, standard_take_num: int, limit:int = 3):
        result = aliased(TakeAIResults)
        coverage = aliased(TakeAIResults)

        rows = self.db.execute(
            select(result.payload, coverage.payload)
            .select_from(Take)
            .join(result, (result.take_id == Take.id) & (result.kind == "TAKE_RESULT"))
            .outerjoin(coverage, (coverage.take_id == Take.id) & (coverage.kind == "SCRIPT_COVERAGE"))
            .where(
                Take.pitch_id == self.get_pitch_id_of_take(take_id),
                Take.take_number < standard_take_num
            )
            .order_by(Take.take_number.desc())
            .limit(limit)
        ).all()

        return [(take_result, script_coverage) for take_result, script_coverage in rows]

    def get_missions_in_take(self, take_id: uuid.UUID) -> list[Mission]:
        """take_id 의 Take 를 보고 만든 미션 (다음 Take 에서 보여 줄 것)."""
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.source_take_id == take_id)
                .order_by(Mission.priority, Mission.slide_number)
            )
        )

    def get_take_infos(self, take_id: uuid.UUID, kind:str = None) -> TakeAIResult | None:
        kind_filter = True if kind is None else (TakeAIResults.kind == kind)
        
        return self.db.scalar(
            select(TakeAIResult.payload).where(
                TakeAIResults.take_id == take_id,
                kind_filter
            )
        )

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
