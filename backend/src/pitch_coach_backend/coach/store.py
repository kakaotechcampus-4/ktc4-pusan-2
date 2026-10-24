"""짧은 DB 세션으로 코치 입력과 이벤트를 읽고 저장한다."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.coach.client import FinalizeReply, PlanReply
from pitch_coach_backend.coach.requests import (
    FinalizeSource,
    TakeConstants,
    TakeContext,
    build_plan,
    missions_payload,
    plan_basis,
    plan_for_evaluate,
)
from pitch_coach_backend.core.database import SessionPerCall
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.dto import StoredEvent, TakeCoachValues


class DbCoachStore(SessionPerCall):

    async def append_events(self, take_id: uuid.UUID, events: Sequence[StoredEvent]) -> None:
        await run_in_threadpool(self._with, coach_service.append_coach_events, take_id, events)

    async def load_events(self, take_id: uuid.UUID) -> list[dict[str, Any]] | None:
        return await run_in_threadpool(self._with, coach_service.load_coach_events, take_id)


class CoachStore(Protocol):
    async def append_events(self, take_id: uuid.UUID, events: Sequence[StoredEvent]) -> None:
        """evaluate 응답 하나의 이벤트를 저장한다. Take 가 지워졌으면 `NonExistentTake`."""
        ...

    async def load_events(self, take_id: uuid.UUID) -> list[dict[str, Any]] | None:
        """저장된 이벤트를 받은 순서대로. Take 결과를 기다리는 중이 아니면(지움 · 끝남) None."""
        ...

    async def load_finalize_source(self, take_id: uuid.UUID) -> FinalizeSource | None:
        """finalize 를 DB 에서 다시 만들 재료. Take 결과를 기다리는 중이 아니면 None."""
        ...

    async def save_take_result(
        self, take_id: uuid.UUID, reply: FinalizeReply, events: Sequence[StoredEvent]
    ) -> bool: ...

    async def fail_take_result(self, take_id: uuid.UUID) -> bool: ...

    async def finalizing_takes(self, stale_before: datetime | None = None) -> list[uuid.UUID]:
        """Take 결과를 기다리는 Take. `stale_before` 를 주면 그 전에 기다리기 시작한 것만."""
        ...

    async def build_plan_request(self, pitch_id: uuid.UUID) -> dict[str, Any] | None: ...

    async def save_plan(
        self,
        pitch_id: uuid.UUID,
        request: Mapping[str, Any],
        reply: PlanReply,
        requested_at: datetime,
    ) -> bool: ...

    async def purge_expired_raw(self, now: datetime) -> int: ...

def load_take_context(db: Session, take_id: uuid.UUID) -> TakeContext | None:
    """발표 중 코치가 들고 있을 값 (architecture.md 1-3 1번). Take 가 없으면 None."""
    values = coach_service.take_coach_values(db, take_id)
    if values is None:
        return None
    return replace(
        _take_context(db, values), seconds=coach_service.stored_metric_seconds(db, take_id)
    )

def _take_context(db: Session, values: TakeCoachValues) -> TakeContext:
    timing = {
        "time_limit_sec": values.time_limit_sec,
        "lower_deviation_sec": values.lower_deviation_sec,
        "upper_deviation_sec": values.upper_deviation_sec,
    }
    basis = plan_basis(
        source_take_id=values.source_take_id, script_version_id=values.script_version_id, **timing
    )
    chosen = coach_service.chosen_plan_payload(
        db,
        values.pitch_id,
        source_take_id=values.source_take_id,
        script_version_id=values.script_version_id,
        target_ms=basis.target_ms,
        min_ms=basis.min_ms,
        max_ms=basis.max_ms,
    )
    constants = TakeConstants(
        take_id=values.take_id,
        mode=values.mode,
        plan=build_plan(
            values.goal_time_sec,
            slides=values.slides,
            parsed_target_ms=values.parsed_target_ms,
            **timing,
        ),
        missions=missions_payload(values.missions),
        recurring_issues=list(values.recurring_issues),
        coaching_plan=plan_for_evaluate(chosen),
        script_used=values.script_used,
        base_level_db=values.base_level_db,
        script_mode=values.script_mode,
    )
    return TakeContext(constants=constants, slides=values.slide_events)
