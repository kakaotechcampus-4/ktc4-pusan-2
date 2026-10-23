import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, Subquery, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeSummary,
    TakeTranscriptSegment,
)

# 아직 끝나지 않은 Take. 실시간 STT 가 붙을 수 있는 상태와 같다 (realtime/service.py)
IN_PROGRESS_STATUSES = ("READY", "RUNNING")


def last_final_saved_at() -> Subquery:
    """Take 별로 마지막 확정 전사를 저장한 시각. `take_id` 로 outer join 한다."""
    return (
        select(
            TakeTranscriptSegment.take_id,
            func.max(TakeTranscriptSegment.created_at).label("saved_at"),
        )
        .group_by(TakeTranscriptSegment.take_id)
        .subquery()
    )


def last_active_at(last_final: Subquery) -> ColumnElement[datetime]:
    """만든 시각 · 시작 시각 · 마지막 확정 전사 저장 시각 중 가장 늦은 것."""
    # GREATEST 는 NULL 을 건너뛴다 (시작 전 · 전사 없음)
    return func.greatest(Take.created_at, Take.started_at, last_final.c.saved_at)


class TakeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_take(self, take_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take).where(Take.id == take_id)
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

    def get_missions_in_take(self, take_id: uuid.UUID) -> list[Mission]:
        """take_id 의 Take 를 보고 만든 미션 (다음 Take 에서 보여 줄 것)."""
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.source_take_id == take_id)
                .order_by(Mission.priority, Mission.slide_number)
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

    def mark_running(self, take_id: uuid.UUID) -> bool:
        """`READY` 인 Take 를 `RUNNING` 으로 바꾼다. 바꿨으면 True.

        이미 `RUNNING` 이거나(다시 붙은 연결) 끝났으면 아무것도 바꾸지 않고 False.
        """
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status == "READY")
            .values(status="RUNNING")
        )
        return result.rowcount > 0

    def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
        """진행 중인 Take 를 끝난 상태(`ANALYZING`)로 바꾼다. 바꿨으면 True.

        BE 가 자동으로 끝냈으면 `end_reason` 에 이유를 같이 적는다 (사용자가 끝냈으면 None).
        Take 가 없거나 이미 끝났으면 아무것도 바꾸지 않고 False. 읽고 쓰지 않고 UPDATE 하나로
        한다 — 같은 Take 를 두 번 끝내도 한 번만 바뀌고, 먼저 적힌 이유가 남는다.
        `ended_at` 은 FE 가 종료 흐름에서 PUT 으로 적는다. 그게 실패하거나 FE 가 떨어져 있어도
        남도록 비어 있을 때만 지금 시각으로 채운다 (뒤에 온 FE 값은 그대로 덮는다).
        """
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status.in_(IN_PROGRESS_STATUSES))
            .values(
                status="ANALYZING",
                end_reason=end_reason,
                ended_at=func.coalesce(Take.ended_at, func.now()),
            )
        )
        return result.rowcount > 0

    def mark_abandoned(self, take_id: uuid.UUID, ended_at: datetime) -> bool:
        """사용자가 떠난 Take 를 `FAILED` 로 바꾸고 이유를 `ABANDONED` 로 적는다. 바꿨으면 True.

        탭을 닫고 돌아오지 않은 Take 다. 시선 기록 · 끝난 시각을 FE 가 보내지 못해 분석하지 않는다.
        `ended_at` 은 이걸 적는 지금이 아니라 마지막으로 연결이 끊긴 시각이다.
        `mark_ended` 처럼 UPDATE 하나로, 진행 중일 때만 바꾼다.
        """
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status.in_(IN_PROGRESS_STATUSES))
            .values(
                status="FAILED",
                end_reason="ABANDONED",
                ended_at=func.coalesce(Take.ended_at, ended_at),
            )
        )
        return result.rowcount > 0

    def running_takes_last_active(self) -> list[tuple[uuid.UUID, datetime]]:
        """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는 데 쓴다.

        연결이 끊긴 시각은 메모리에만 있어서 다시 뜨면 없다. 그래서 만든 시각 · 시작 시각 ·
        마지막 확정 전사를 저장한 시각 중 가장 늦은 것으로 대신한다.
        """
        last_final = last_final_saved_at()
        rows = self.db.execute(
            select(Take.id, last_active_at(last_final))
            .outerjoin(last_final, last_final.c.take_id == Take.id)
            .where(Take.status == "RUNNING")
        ).all()
        return [(take_id, last_active_at) for take_id, last_active_at in rows]
