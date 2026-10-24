"""Take 시작 값 고정과 미션 선택. 커밋은 service가 담당한다."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import (
    Mission,
    Take,
)
from pitch_coach_backend.module.take.repository import (
    TakeRepository,
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
