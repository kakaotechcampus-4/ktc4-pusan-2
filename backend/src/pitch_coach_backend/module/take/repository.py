import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased, defer

from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeAIResults,
    TakeSummary,
    TakeTranscriptSegment,
)

# 아직 끝나지 않은 Take. 실시간 STT 가 붙을 수 있는 상태와 같다 (realtime/service.py)
IN_PROGRESS_STATUSES = ("READY", "RUNNING")


def last_active_at() -> ColumnElement[datetime]:
    """만든 시각 · 시작 시각 · 마지막 확정 전사 저장 시각 중 가장 늦은 것."""
    # Take 마다 `take_id` 인덱스로 찾는다. 전사 표 전체를 묶으면 끝난 Take 의 전사까지 읽는다
    last_final = (
        select(func.max(TakeTranscriptSegment.created_at))
        .where(TakeTranscriptSegment.take_id == Take.id)
        .scalar_subquery()
    )
    # GREATEST 는 NULL 을 건너뛴다 (시작 전 · 전사 없음)
    return func.greatest(Take.created_at, Take.started_at, last_final)


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
            # null인 score는 제일 뒤로 변경. 가장 낮은 점수로 취급한다.
            .order_by(TakeSummary.score.desc().nulls_last())
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

    def get_by_client_session(
        self, pitch_id: uuid.UUID, client_session_id: uuid.UUID
    ) -> Take | None:
        return self.db.scalar(
            select(Take)
            .where(Take.pitch_id == pitch_id, Take.client_session_id == client_session_id)
            .options(defer(Take.event_logs))
        )

    def abandon_ready_takes(self, pitch_id: uuid.UUID) -> int:
        """이 pitch 에서 `READY` 로 남은 Take 를 `FAILED`(`ABANDONED`)로 바꾼다. 바꾼 수를 돌려준다.

        새 Take 를 만들 때 부른다. FE 는 가장 최근 Take 로만 발표하므로 앞의 `READY` 는 다시 쓰이지
        않는다. 시간으로 끝내지 않는 이유: 마이크 없이 하는 발표는 실시간 연결이 없어 끝날 때까지
        `READY` 다. 끝난 시각은 알 수 없어 만든 시각으로 적는다.
        """
        result = self.db.execute(
            update(Take)
            .where(Take.pitch_id == pitch_id, Take.status == "READY")
            .values(
                status="FAILED",
                end_reason="ABANDONED",
                ended_at=func.coalesce(Take.ended_at, Take.created_at),
            )
        )
        return result.rowcount

    def next_take_number(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(Take.take_number)).where(Take.pitch_id == pitch_id)
        )
        return (current or 0) + 1

    def get_latest_take_in_pitch(self, pitch_id: uuid.UUID) -> Take | None:
        # 부르는 곳(장치 점검)은 번호 · 대본 표시 방식만 본다. 시선 1초 기록이 쌓이는
        # event_logs 는 읽지 않는다
        return self.db.scalar(
            select(Take)
            .where(Take.pitch_id == pitch_id)
            .options(defer(Take.event_logs))
            # created_at 은 같은 트랜잭션 안에서 같아질 수 있다. 번호는 pitch 안에서 유일하다
            .order_by(Take.take_number.desc())
            .limit(1)
        )

    def get_latest_reviewed_take_in_pitch(self, pitch_id: uuid.UUID) -> Take | None:
        # 이전 미션은 리뷰까지 끝난 Take 중 가장 최근 것에서 가져온다
        return self.db.scalar(
            select(Take)
            .where(Take.pitch_id == pitch_id, Take.review_status == "DONE")
            .options(defer(Take.event_logs))
            .order_by(Take.take_number.desc())
            .limit(1)
        )

    def get_take_number(self, take_id: uuid.UUID) -> int | None:
        return self.db.scalar(
            select(Take.take_number).where(Take.id == take_id)
        )

    def get_previous_take_results(self, take_id: uuid.UUID, standard_take_num: int, limit:int = 3):
        result = aliased(TakeAIResults)
        coverage = aliased(TakeAIResults)

        rows = self.db.execute(
            select(result.payload, coverage.payload)
            .select_from(Take)
            .join(result, (result.take_id == Take.id) & (result.kind == "TAKE_RESULT"))
            .outerjoin(coverage, (coverage.take_id == Take.id) 
                       & (coverage.kind == "SCRIPT_COVERAGE"))
            .where(
                Take.pitch_id == self.get_pitch_id_of_take(take_id),
                Take.take_number < standard_take_num
            )
            .order_by(Take.take_number.desc())
            .limit(limit)
        ).all()

        return [(take_result, script_coverage) for take_result, script_coverage in rows]

    def get_previous_review_payload(self, 
                                    take_id: uuid.UUID, standard_take_num: int) -> dict | None:
        return self.db.scalar(
            select(TakeAIResults.payload)
            .join(Take, TakeAIResults.take_id == Take.id)
            .where(
                Take.pitch_id == self.get_pitch_id_of_take(take_id),
                Take.take_number < standard_take_num,
                TakeAIResults.kind == "REVIEW",
            )
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

    def get_missions_for_take(self, take_id: uuid.UUID) -> list[Mission]:
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.take_id == take_id)
                .order_by(Mission.priority, Mission.slide_number)
            )
        )

    def assign_missions_to_take(self, source_take_id: uuid.UUID, take_id: uuid.UUID) -> None:
        self.db.execute(
            update(Mission)
            .where(Mission.source_take_id == source_take_id, Mission.status == "ACTIVE")
            .values(take_id=take_id)
        )

    def get_take_infos(self, take_id: uuid.UUID, kind:str = None) -> TakeAIResults | None:
        kind_filter = True if kind is None else (TakeAIResults.kind == kind)
        
        return self.db.scalar(
            select(TakeAIResults.payload).where(
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

    def save_total_result(self, 
                          take_id: uuid.UUID, total_result_json: dict, meta_json: dict | None
                          ) -> TakeAIResults:
        values = {
            "take_id": take_id,
            "kind": "REVIEW",
            "payload": total_result_json,
            "meta": meta_json
        }
        stmt = insert(TakeAIResults).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TakeAIResults.take_id, TakeAIResults.kind],
            set_={key: stmt.excluded[key] for key in values if key not in ("take_id", "kind")},
        ).returning(TakeAIResults)
        return self.db.scalars(stmt, execution_options={"populate_existing": True}).one()

    def upsert_next_missions(self, missions: list[dict[str, Any]]) -> list[Mission]:
        """다음 Take 에서 보여 줄 미션을 upsert 한다. source_take_id 는 take_id 로 고정이다."""
        if not missions:
            return []

        stmt = insert(Mission).values(missions)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Mission.source_take_id, Mission.priority],
            set_={
                key: stmt.excluded[key] 
                for key in ("area", "slide_number", "target", "description", "reason")
                },
            where=Mission.status == "ACTIVE",
        ).returning(Mission)
        return self.db.scalars(stmt, execution_options={"populate_existing": True}).all()

    def update_mission_result(self,
                               mission_id: uuid.UUID, take_id: uuid.UUID, status: str, 
                               observed: float | None) -> Mission:
        self.db.execute(
            update(Mission)
            .where(Mission.id == mission_id, Mission.take_id == take_id)
            .values(status=status, observed=observed, complete=(status == "ACHIEVED"))
        )

    def upsert_summary_review(self, 
                              take_id: uuid.UUID, summary: str, score: int | None) -> TakeSummary:
        stmt = insert(TakeSummary).values(
            take_id=take_id, summary=summary, score=score
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[TakeSummary.take_id],
            set_={"summary": stmt.excluded.summary, "score": stmt.excluded.score},
        )
        self.db.execute(stmt)

    def set_analysis_stage(self, take_id: uuid.UUID, stage: str) -> None:
        self.db.execute(
            update(Take)
            .where(Take.id == take_id)
            .values(analysis_stage=stage, analysis_stage_at=func.now())
        )

    def complete_take(self, take_id: uuid.UUID, review_status: str) -> None:
        self.db.execute(
            update(Take)
            .where(Take.id == take_id)
            .values(status="COMPLETED", review_status=review_status)
        )
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

        떠난 것으로 끝낸 Take(`FAILED` · `ABANDONED`)도 되살린다. 재연결을 포기하고 STT 없이
        발표를 이어 간 FE 가 끝내기 PUT 을 보낸 것이다 — 기다리는 시간보다 길게 발표했다.
        """
        abandoned = and_(Take.status == "FAILED", Take.end_reason == "ABANDONED")
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, or_(Take.status.in_(IN_PROGRESS_STATUSES), abandoned))
            .values(
                status="ANALYZING",
                end_reason=end_reason,
                ended_at=func.coalesce(Take.ended_at, func.now()),
            )
        )
        return result.rowcount > 0

    def mark_abandoned(
        self,
        take_id: uuid.UUID,
        ended_at: datetime | ColumnElement[datetime],
        end_reason: str = "ABANDONED",
    ) -> bool:
        """사용자가 떠난 Take 를 `FAILED` 로 바꾸고 이유를 `end_reason` 으로 적는다. 바꿨으면 True.

        창을 닫았거나(`CLOSED`) 연결이 끊긴 채 돌아오지 않은(`ABANDONED`) Take 다. 시선 기록 · 끝난
        시각을 FE 가 보내지 못해 분석하지 않는다.
        `ended_at` 은 이걸 적는 지금이 아니라 마지막으로 연결이 끊긴 시각이다.
        `mark_ended` 처럼 UPDATE 하나로, 진행 중일 때만 바꾼다.
        """
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status.in_(IN_PROGRESS_STATUSES))
            .values(
                status="FAILED",
                end_reason=end_reason,
                ended_at=func.coalesce(Take.ended_at, ended_at),
            )
        )
        return result.rowcount > 0

    def running_takes_last_active(self) -> list[tuple[uuid.UUID, datetime]]:
        """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는 데 쓴다.

        연결이 끊긴 시각은 메모리에만 있어서 다시 뜨면 없다. 그래서 만든 시각 · 시작 시각 ·
        마지막 확정 전사를 저장한 시각 중 가장 늦은 것으로 대신한다.
        """
        rows = self.db.execute(
            select(Take.id, last_active_at()).where(Take.status == "RUNNING")
        ).all()
        return [(take_id, last_active_at) for take_id, last_active_at in rows]
