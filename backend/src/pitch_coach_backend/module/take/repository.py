import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Row, and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased, defer

from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
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

    def get_owned_with_pitch_title(
        self, take_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Take, str] | None:
        row = self.db.execute(
            select(Take, Pitch.title)
            .join(Pitch, Pitch.id == Take.pitch_id)
            .where(Take.id == take_id, Pitch.user_id == user_id)
        ).first()
        return None if row is None else (row[0], row[1])

    def get_ai_payloads(self, take_id: uuid.UUID, kinds: tuple[str, ...]) -> dict[str, Any]:
        rows = self.db.execute(
            select(TakeAIResults.kind, TakeAIResults.payload).where(
                TakeAIResults.take_id == take_id, TakeAIResults.kind.in_(kinds)
            )
        ).all()
        return {kind: payload for kind, payload in rows}

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
        않는다.
        """
        return self._abandon_ready(Take.pitch_id == pitch_id)

    def abandon_stale_ready_takes(self, older_than: timedelta) -> int:
        """만든 지 `older_than` 이 지나도 `READY` 인 Take 를 `FAILED`(`ABANDONED`)로 바꾼다.

        FE 는 첫 실시간 연결이 `ready` 를 받은 뒤에 발표를 시작하고, 그 연결이 `RUNNING` 으로
        바꾼다. 그래서 `READY` 는 시작하지 않은 Take 다 — 장치 점검에서 시작을 누른 뒤 연결이 끝내
        붙지 않았다. 시각은 DB 시계로 잰다. 같은 순간 붙은 연결(`mark_running`)과는 둘 다 조건부
        UPDATE 라 먼저 적은 쪽이 남는다.
        """
        return self._abandon_ready(Take.created_at < func.now() - older_than)

    def _abandon_ready(self, *where: ColumnElement[bool]) -> int:
        # 끝난 시각은 알 수 없어 만든 시각으로 적는다
        result = self.db.execute(
            update(Take)
            .where(Take.status == "READY", *where)
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

    def get_latest_reviewed_take_in_pitch(
        self, pitch_id: uuid.UUID, *, before: int | None = None
    ) -> Take | None:
        """리뷰까지 끝난 Take 중 가장 늦게 끝난 것. `before` 가 있으면 번호가 그보다 작은 것만 본다.

        다음 Take 에 실을 미션이 이 Take 의 리뷰에서 나온다 (`docs/ai/features/review-agent.md`
        12-2). 장치 점검 화면과 Take 를 시작할 때 미션을 싣는 쪽(`coach_repository`)이 이 함수
        하나로 고른다. 리뷰 결과(`REVIEW`)와 `review_status` `DONE` 은 한 트랜잭션에 적힌다.
        """
        query = (
            select(Take)
            .where(Take.pitch_id == pitch_id, Take.review_status == "DONE")
            .options(defer(Take.event_logs))
        )
        if before is not None:
            query = query.where(Take.take_number < before)
        return self.db.scalar(query.order_by(*finished_order()).limit(1))

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
                Take.take_number < standard_take_num,
                Take.status == "COMPLETED",
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
            .order_by(*finished_order())
            .limit(1)
        )

    def get_mission_ids_of_take(self, take_id: uuid.UUID) -> list[uuid.UUID]:
        mission_ids = self.db.scalar(select(Take.mission_ids).where(Take.id == take_id))
        return [uuid.UUID(mission_id) for mission_id in mission_ids or []]

    def get_missions_by_ids(self, mission_ids: list[uuid.UUID]) -> list[Mission]:
        if not mission_ids:
            return []
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.id.in_(mission_ids))
                .order_by(Mission.priority, Mission.slide_number)
            )
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

    def save_metric_seconds(self, take_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
        """1초 원자료를 한 번에 넣는다. 이미 있는 1초는 건너뛴다 — 먼저 저장한 것을 둔다."""
        self.db.execute(
            insert(TakeMetricSecond)
            .values([{"take_id": take_id, **row} for row in rows])
            .on_conflict_do_nothing(index_elements=["take_id", "t_ms"])
        )

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
            set_={
                **{key: stmt.excluded[key] for key in values if key not in ("take_id", "kind")},
                # payload 를 바꿀 때마다 1 올린다 (script-coverage-evaluation.md 12-1)
                "revision": TakeAIResults.revision + 1,
            },
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

    def lock_take(self, take_id: uuid.UUID) -> Take | None:
        return self.db.scalar(
            select(Take)
            .where(Take.id == take_id)
            .options(defer(Take.event_logs))
            .with_for_update()
        )

    def get_ai_result(self, take_id: uuid.UUID, kind: str) -> TakeAIResults | None:
        return self.db.scalar(
            select(TakeAIResults).where(
                TakeAIResults.take_id == take_id, TakeAIResults.kind == kind
            )
        )

    def insert_ai_result(
        self, take_id: uuid.UUID, kind: str, payload: dict, meta: dict | None
    ) -> bool:
        inserted = self.db.scalar(
            insert(TakeAIResults)
            .values(take_id=take_id, kind=kind, payload=payload, meta=meta, revision=0)
            .on_conflict_do_nothing(index_elements=[TakeAIResults.take_id, TakeAIResults.kind])
            .returning(TakeAIResults.id)
        )
        return inserted is not None

    def replace_ai_result(
        self, take_id: uuid.UUID, kind: str, base_revision: int, payload: dict, meta: dict | None
    ) -> bool:
        result = self.db.execute(
            update(TakeAIResults)
            .where(
                TakeAIResults.take_id == take_id,
                TakeAIResults.kind == kind,
                TakeAIResults.revision == base_revision,
            )
            .values(payload=payload, meta=meta, revision=TakeAIResults.revision + 1)
        )
        return result.rowcount > 0

    def delete_ai_result(self, take_id: uuid.UUID, kind: str) -> None:
        self.db.execute(
            delete(TakeAIResults).where(
                TakeAIResults.take_id == take_id, TakeAIResults.kind == kind
            )
        )

    def upsert_summary_speak_accuracy(self, take_id: uuid.UUID, value: float | None) -> None:
        stmt = insert(TakeSummary).values(take_id=take_id, speak_accuracy=value)
        self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[TakeSummary.take_id],
                set_={"speak_accuracy": stmt.excluded.speak_accuracy},
            )
        )

    def get_slide_events(self, take_id: uuid.UUID) -> list[tuple[int, int]]:
        rows = self.db.execute(
            select(TakeSlideEvent.slide_number, TakeSlideEvent.started_ms).where(
                TakeSlideEvent.take_id == take_id
            )
        ).all()
        return [(slide_number, started_ms) for slide_number, started_ms in rows]

    def get_transcript_words(self, take_id: uuid.UUID) -> list[tuple[str, int]]:
        segments = self.db.scalars(
            select(TakeTranscriptSegment.words)
            .where(TakeTranscriptSegment.take_id == take_id)
            .order_by(TakeTranscriptSegment.seq)
        ).all()
        return [
            (word.get("punctuated_word") or word["word"], word["start_ms"])
            for words in segments
            for word in words
        ]

    def complete_take(self, take_id: uuid.UUID, review_status: str) -> None:
        # completed_at 은 '가장 늦게 끝난 Take' 의 순서다 (docs/ai/architecture.md 1-3 'Take 상태')
        self.db.execute(
            update(Take)
            .where(Take.id == take_id)
            .values(
                status="COMPLETED",
                review_status=review_status,
                completed_at=func.now(),
                review_request_id=None,
            )
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

    def mark_ended(
        self,
        take_id: uuid.UUID,
        end_reason: str | None = None,
        utterance_ends: list[int] | None = None,
    ) -> bool:
        """발표 중인(`RUNNING`) Take 를 끝난 상태(`ANALYZING`)로 바꾼다. 바꿨으면 True.

        `READY` 는 바꾸지 않는다. 실시간 연결이 한 번도 붙지 않아 남은 기록이 없다.

        BE 가 자동으로 끝냈으면 `end_reason` 에 이유를 같이 적는다 (사용자가 끝냈으면 None).
        Take 가 없거나 이미 끝났으면 아무것도 바꾸지 않고 False. 읽고 쓰지 않고 UPDATE 하나로
        한다 — 같은 Take 를 두 번 끝내도 한 번만 바뀌고, 먼저 적힌 이유가 남는다.
        `ended_at` 은 FE 가 종료 흐름에서 PUT 으로 적는다. 그게 실패하거나 FE 가 떨어져 있어도
        남도록 비어 있을 때만 지금 시각으로 채운다 (뒤에 온 FE 값은 그대로 덮는다).

        떠난 것으로 끝낸 Take(`FAILED` · `ABANDONED`)도 되살린다. 재연결을 포기하고 STT 없이
        발표를 이어 간 FE 가 끝내기 PUT 을 보낸 것이다 — 기다리는 시간보다 길게 발표했다.

        분석 단계는 `FINALIZING` 이다 — 이어서 Take 결과(finalize)를 만든다. 문장 끝 신호가
        주어지면 같이 쓰고, 없으면(메모리에 없음) 있던 값을 둔다.
        """
        abandoned = and_(Take.status == "FAILED", Take.end_reason == "ABANDONED")
        values: dict[str, Any] = {
            "status": ENDED_STATUS,
            "end_reason": end_reason,
            "ended_at": func.coalesce(Take.ended_at, func.now()),
            "analysis_stage": FINALIZING_STAGE,
            "analysis_stage_at": func.now(),
        }
        if utterance_ends is not None:
            values["utterance_ends"] = utterance_ends
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, or_(Take.status == "RUNNING", abandoned))
            .values(**values)
        )
        return result.rowcount > 0

    def mark_abandoned(
        self,
        take_id: uuid.UUID,
        ended_at: datetime | ColumnElement[datetime],
        end_reason: str = "ABANDONED",
        utterance_ends: list[int] | None = None,
    ) -> bool:
        """사용자가 떠난 Take 를 `FAILED` 로 바꾸고 이유를 `end_reason` 으로 적는다. 바꿨으면 True.

        창을 닫았거나(`CLOSED`) 연결이 끊긴 채 돌아오지 않은(`ABANDONED`) Take 다. 시선 기록 · 끝난
        시각을 FE 가 보내지 못해 분석하지 않는다.
        `ended_at` 은 이걸 적는 지금이 아니라 마지막으로 연결이 끊긴 시각이다.
        `mark_ended` 처럼 UPDATE 하나로, 진행 중일 때만 바꾼다.
        """
        values: dict[str, Any] = {
            "status": "FAILED",
            "end_reason": end_reason,
            "ended_at": func.coalesce(Take.ended_at, ended_at),
        }
        if utterance_ends is not None:
            values["utterance_ends"] = utterance_ends
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status.in_(IN_PROGRESS_STATUSES))
            .values(**values)
        )
        return result.rowcount > 0

    def list_started_takes(
        self, user_id: uuid.UUID, pitch_id: uuid.UUID | None = None
    ) -> list[Row[Any]]:
        """사용자의 시작한 Take 를 시작 시각 최신순으로 읽는다. `pitch_id` 를 주면 그 발표 것만.

        시작하지 않은 Take(장치 점검에서 버려진 것)는 시작 시각이 없어 빠진다. 한 줄에 Take 기록
        화면이 쓰는 값을 모두 싣는다 — 집계 · 장치 점검 결과가 없으면 그 칸은 null 이다.
        """
        # 미션 행은 다음 Take 로 옮겨질 수 있다. 판정은 각 Take 의 저장 리뷰에서 읽는다.
        # 시작 전 Take 는 칸이 비어 있다. JSON null 이 들어 있어도 배열 길이를 묻지 않는다
        is_array = func.jsonb_typeof(Take.mission_ids) == "array"
        total = case((is_array, func.jsonb_array_length(Take.mission_ids)), else_=0)
        stmt = (
            select(
                Take.id.label("take_id"),
                Take.pitch_id,
                Pitch.title.label("pitch_title"),
                Take.take_number,
                Take.started_at,
                Take.status,
                Take.script_mode,
                Take.duration_sec,
                Take.goal_time_sec,
                TakeSummary.audience_gaze,
                TakeSummary.script_gaze,
                TakeSummary.average_cpm,
                TakeSummary.filler_count,
                Take.mission_ids,
                TakeAIResults.payload["mission_results"].label("mission_results"),
                total.label("missions_total"),
                Calibration.result["engine_version"].astext.label("gaze_engine"),
            )
            .join(Pitch, Pitch.id == Take.pitch_id)
            .outerjoin(TakeSummary, TakeSummary.take_id == Take.id)
            .outerjoin(Calibration, Calibration.take_id == Take.id)
            .outerjoin(
                TakeAIResults,
                (TakeAIResults.take_id == Take.id) & (TakeAIResults.kind == "REVIEW"),
            )
            .where(Pitch.user_id == user_id, Take.started_at.is_not(None))
            .order_by(Take.started_at.desc(), Take.take_number.desc())
        )
        if pitch_id is not None:
            stmt = stmt.where(Take.pitch_id == pitch_id)
        return list(self.db.execute(stmt).all())

    def running_takes_last_active(self) -> list[tuple[uuid.UUID, datetime]]:
        """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는 데 쓴다.

        연결이 끊긴 시각은 메모리에만 있어서 다시 뜨면 없다. 그래서 만든 시각 · 시작 시각 ·
        마지막 확정 전사를 저장한 시각 중 가장 늦은 것으로 대신한다.
        """
        rows = self.db.execute(
            select(Take.id, last_active_at()).where(Take.status == "RUNNING")
        ).all()
        return [(take_id, last_active_at) for take_id, last_active_at in rows]

    def start_review(self, take_id: uuid.UUID, request_id: uuid.UUID | None = None) -> bool:
        """채점까지 끝난 Take 의 분석 단계를 `REVIEWING` 으로 바꾼다. 바꿨으면 True.

        채점이 REVIEWING으로 넘긴 뒤 아직 실행 표식이 없으면 즉시 시작한다.
        Take 결과 · 채점을 만드는 중(`FINALIZING` · `SCORING`)이거나 이미 리뷰 중 · 끝난 Take 는
        바꾸지 않는다 — 덮으면
        그 Take 결과가 버려지거나 리뷰를 두 번 부른다 (`docs/ai/architecture.md` 1-3 분석 단계 표).
        다만 `REVIEW_STALE_AFTER` 넘게 리뷰 중인 Take 는 그 리뷰가 끊긴 것이라 다시 받는다 — 안
        받으면 그 Take 는 끝내 리뷰를 못 받는다. 분석 단계가 없는 Take 는 그 칸이 생기기 전에 끝난
        것이라 예전처럼 받는다.
        """
        stale_review = and_(
            Take.analysis_stage == "REVIEWING",
            Take.analysis_stage_at < func.now() - REVIEW_STALE_AFTER,
        )
        result = self.db.execute(
            update(Take)
            .where(
                Take.id == take_id,
                Take.status == "ANALYZING",
                Take.analysis_hold_reason.is_(None),
                ~select(TakeAIResults.id)
                .where(
                    TakeAIResults.take_id == take_id,
                    TakeAIResults.kind == "SCRIPT_COVERAGE_PENDING",
                )
                .exists(),
                or_(
                    and_(
                        Take.analysis_stage == "REVIEWING", Take.review_request_id.is_(None)
                    ),
                    Take.analysis_stage == "AWAITING_CONFIRMATION",
                    Take.analysis_stage.is_(None),
                    stale_review,
                ),
            )
            .values(
                analysis_stage="REVIEWING",
                analysis_stage_at=func.now(),
                review_request_id=request_id or uuid.uuid4(),
            )
        )
        return result.rowcount > 0

    def lock_review(self, take_id: uuid.UUID, request_id: uuid.UUID) -> Take | None:
        """현재 실행 중인 리뷰만 잠근다. 오래된 응답·삭제된 Take는 저장하지 않는다."""
        return self.db.scalar(
            select(Take)
            .where(
                Take.id == take_id,
                Take.status == "ANALYZING",
                Take.analysis_stage == "REVIEWING",
                Take.review_request_id == request_id,
                Take.analysis_hold_reason.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def utterance_ends(self, take_id: uuid.UUID) -> list[int]:
        """저장된 문장 끝 신호. 없으면 빈 목록."""
        return json_ints(self.db.scalar(select(Take.utterance_ends).where(Take.id == take_id)))

    def save_utterance_ends(self, take_id: uuid.UUID, utterance_ends: list[int]) -> bool:
        """진행 중인 Take 의 문장 끝 신호를 바꾼다. 끝난 Take 는 끝낼 때 적은 값을 둔다."""
        result = self.db.execute(
            update(Take)
            .where(Take.id == take_id, Take.status.in_(IN_PROGRESS_STATUSES))
            .values(utterance_ends=utterance_ends)
        )
        return result.rowcount > 0

    def raw_data_open(self, take_id: uuid.UUID) -> bool | None:
        """1초 원자료를 더 넣어도 되는 Take 인가. Take 가 없으면 None.

        Take 결과를 저장한 뒤(분석 단계가 `FINALIZING` 을 지남)에는 넣지 않는다 — 동의 없는 Take 는
        Take 결과를 저장할 때 1초 원자료를 지우는데, 그 뒤에 늦게 들어간 행은 끝내 남는다. Take 행을
        `FOR SHARE` 로 잠가, Take 결과를 저장하는 트랜잭션(`FOR UPDATE`)과 줄을 세운다.

        Take 결과를 만들지 못한 Take(`FAILED`, 단계는 `FINALIZING` 그대로)와 떠난 Take(`FAILED`,
        단계 없음)는 받는다. 나중에 replay 로 다시 만들 수 있게 원자료를 두는 Take 라 늦게 온 행도
        그 재료다. 동의가 없으면 7일 뒤 함께 지운다 (architecture.md 1-1 '지우기 · 보관').
        """
        stage = self.db.execute(
            select(Take.analysis_stage).where(Take.id == take_id).with_for_update(read=True)
        ).first()
        if stage is None:
            return None
        return stage[0] in (None, FINALIZING_STAGE)


REVIEW_STALE_AFTER = timedelta(minutes=2)

def finished_order() -> tuple[ColumnElement[Any], ...]:
    """'가장 늦게 끝난 Take' 순서 (`docs/ai/architecture.md` 1-3 'Take 상태').

    칸을 만들기 전에 끝나 completed_at 이 없는 Take 는 뒤로 가고, 같은 시각이면 번호가 큰 것이다.
    """
    return (Take.completed_at.desc().nulls_last(), Take.take_number.desc())


ENDED_STATUS = "ANALYZING"

FINALIZING_STAGE = "FINALIZING"

def json_ints(values: Any) -> list[int]:
    """JSONB 정수 목록(`takes.utterance_ends` 등)을 읽는다. 정수가 아닌 값은 뺀다."""
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
