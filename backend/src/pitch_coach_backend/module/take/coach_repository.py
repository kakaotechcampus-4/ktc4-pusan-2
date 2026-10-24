"""Take 시작 값 고정과 미션 선택. 커밋은 service가 담당한다."""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import (
    Calibration,
    CoachEvent,
    Mission,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import (
    ENDED_STATUS,
    FINALIZING_STAGE,
    TakeRepository,
    finished_order,
)


class CoachRepository:
    def __init__(self, db: Session):
        self.db = db

    def pitch_id_of(self, take_id: uuid.UUID) -> uuid.UUID | None:
        return self.db.scalar(select(Take.pitch_id).where(Take.id == take_id))

    def mark_started(self, take_id: uuid.UUID) -> int | None:
        """`READY` 인 Take 를 `RUNNING` 으로 바꾸고 그 번호를 돌려준다. 바꾸지 않았으면 None."""
        return self.db.scalar(
            update(Take)
            .where(Take.id == take_id, Take.status == "READY")
            .values(status="RUNNING")
            .returning(Take.take_number)
        )

    def assign_missions(
        self, take_id: uuid.UUID, pitch_id: uuid.UUID, *, before: int
    ) -> list[uuid.UUID]:
        """그 발표에서 리뷰가 있는 Take 중 가장 늦게 끝난 Take 의 리뷰가 만든 `ACTIVE` 미션을 이
        Take 에 싣는다 (review-agent.md 12-2). 행의 `take_id` 를 이 Take 로 바꾼다 — 판정은
        `take_id` 가 같은 Take 의 리뷰만 덮는다. 실은 미션 id 를 리뷰가 읽는 순서로 돌려준다."""
        source_take_id = self.latest_reviewed_take(pitch_id, before=before)
        if source_take_id is None:
            return []
        # 리뷰가 미션을 읽는 순서와 같다 (`TakeRepository.get_missions_by_ids`)
        mission_ids = [m.id for m in self.active_missions_from(source_take_id)]
        if mission_ids:
            self.db.execute(
                update(Mission).where(Mission.id.in_(mission_ids)).values(take_id=take_id)
            )
        return mission_ids

    def fix_started_values(
        self,
        take_id: uuid.UUID,
        *,
        mission_ids: list[uuid.UUID],
        standard_version: int | None,
        raw_data_consent: bool,
    ) -> None:
        self.db.execute(
            update(Take)
            .where(Take.id == take_id)
            .values(
                mission_ids=[str(mission_id) for mission_id in mission_ids],
                standard_version=standard_version,
                raw_data_consent=raw_data_consent,
            )
        )

    def latest_reviewed_take(self, pitch_id: uuid.UUID, *, before: int | None) -> uuid.UUID | None:
        """리뷰까지 끝난 Take 중 가장 늦게 끝난 것의 id.

        `TakeRepository.get_latest_reviewed_take_in_pitch` 와 같은 규칙이다 — 장치 점검 화면과
        같은 Take 를 고른다.
        """
        take = TakeRepository(self.db).get_latest_reviewed_take_in_pitch(pitch_id, before=before)
        return take.id if take is not None else None

    def active_missions_from(self, source_take_id: uuid.UUID) -> list[Mission]:
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.source_take_id == source_take_id, Mission.status == "ACTIVE")
                .order_by(Mission.priority, Mission.slide_number, Mission.id)
            )
        )

    def append_slide_events(self, take_id: uuid.UUID, rows: Iterable[Mapping[str, Any]]) -> None:
        """장 변경을 한 번에 넣는다. 같은 시각은 먼저 저장한 것을 둔다."""
        values = [{"take_id": take_id, **row} for row in rows]
        if not values:
            return
        self.db.execute(
            insert(TakeSlideEvent)
            .values(values)
            .on_conflict_do_nothing(index_elements=["take_id", "started_ms"])
        )

    def take(self, take_id: uuid.UUID) -> Take | None:
        # 같은 세션이 Take 를 시작하기 전에 읽어 둔 객체가 있을 수 있다(WS 인가). 시작할 때 고정한
        # 값을 읽어야 하므로 DB 에서 다시 채운다
        return self.db.get(Take, take_id, populate_existing=True)

    def missions(self, mission_ids: Iterable[uuid.UUID]) -> list[Mission]:
        """id 목록의 미션. 지운 미션은 빠진다. 목록의 순서를 지킨다."""
        ids = list(mission_ids)
        if not ids:
            return []
        rows = {m.id: m for m in self.db.scalars(select(Mission).where(Mission.id.in_(ids)))}
        return [rows[i] for i in ids if i in rows]

    def latest_completed_take(self, pitch_id: uuid.UUID, *, before: int | None) -> uuid.UUID | None:
        """`COMPLETED` Take 중 가장 늦게 끝난 것 (리뷰가 끝내 실패한 Take 포함). 코칭 계획의 바탕
        Take 다. `before` 가 있으면 번호가 그보다 작은 것만 본다."""
        query = select(Take.id).where(Take.pitch_id == pitch_id, Take.status == "COMPLETED")
        if before is not None:
            query = query.where(Take.take_number < before)
        return self.db.scalar(query.order_by(*finished_order()).limit(1))

    def ai_result(self, take_id: uuid.UUID, kind: str) -> TakeAIResults | None:
        return self.db.scalar(
            select(TakeAIResults).where(
                TakeAIResults.take_id == take_id, TakeAIResults.kind == kind
            )
        )

    def base_level_db(self, take_id: uuid.UUID) -> float | None:
        return self.db.scalar(
            select(Calibration.base_level_db).where(Calibration.take_id == take_id)
        )

    def slide_events(self, take_id: uuid.UUID) -> list[TakeSlideEvent]:
        return list(
            self.db.scalars(
                select(TakeSlideEvent)
                .where(TakeSlideEvent.take_id == take_id)
                .order_by(TakeSlideEvent.started_ms)
            )
        )

    def metric_seconds(self, take_id: uuid.UUID) -> list[TakeMetricSecond]:
        return list(
            self.db.scalars(
                select(TakeMetricSecond)
                .where(TakeMetricSecond.take_id == take_id)
                .order_by(TakeMetricSecond.t_ms)
            )
        )

    def coach_events(self, take_id: uuid.UUID) -> list[CoachEvent]:
        return list(
            self.db.scalars(
                select(CoachEvent).where(CoachEvent.take_id == take_id).order_by(CoachEvent.seq)
            )
        )

    def append_events(
        self, take_id: uuid.UUID, events: Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]]
    ) -> None:
        """(이벤트, 그 응답의 meta) 를 받은 순서대로 더한다. 같은 `event_id` 는 건너뛴다.

        seq 는 저장된 가장 큰 seq 다음부터다. 같은 Take 의 이벤트 저장은 한 번에 하나라 겹치지
        않는다 (부르는 쪽이 줄을 세운다). 건너뛴 이벤트 자리는 비어도 된다 — 순서만 지키면 된다.
        """
        rows = list(events)
        if not rows:
            return
        # 같은 Take 의 이벤트 저장끼리 · Take 결과 저장과 줄을 세운다 — 둘이 같은 max(seq) 를 읽으면
        # (take_id, seq) 가 부딪힌다
        if self.db.scalar(select(Take.id).where(Take.id == take_id).with_for_update()) is None:
            raise NonExistentTake()
        last = self.db.scalar(
            select(func.coalesce(func.max(CoachEvent.seq), 0)).where(CoachEvent.take_id == take_id)
        )
        self.db.execute(
            insert(CoachEvent)
            .values(
                [
                    {
                        "take_id": take_id,
                        "seq": last + i,
                        "kind": event["kind"],
                        "event_id": event["event_id"],
                        "t_ms": event["t_ms"],
                        "payload": dict(event),
                        "meta": dict(meta),
                    }
                    for i, (event, meta) in enumerate(rows, start=1)
                ]
            )
            .on_conflict_do_nothing(index_elements=["take_id", "event_id"])
        )


REVIEW = "REVIEW"

TAKE_RESULT = "TAKE_RESULT"

def waiting_for_result() -> ColumnElement[bool]:
    """Take 결과(finalize)를 기다리는 Take — 상태 `ANALYZING` · 분석 단계 `FINALIZING`."""
    return and_(Take.status == ENDED_STATUS, Take.analysis_stage == FINALIZING_STAGE)

def is_waiting_for_result(take: Take) -> bool:
    """읽어 둔 Take 에 `waiting_for_result` 를 본다."""
    return take.status == ENDED_STATUS and take.analysis_stage == FINALIZING_STAGE
