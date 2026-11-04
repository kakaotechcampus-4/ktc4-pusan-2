"""짧은 DB 세션으로 코치 입력과 이벤트를 읽고 저장한다."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.coach.coaching_plan import (
    plan_basis,
    plan_for_evaluate,
    plan_payload,
    request_basis,
)
from pitch_coach_backend.coach.replies import FinalizeReply, PlanReply
from pitch_coach_backend.coach.requests import (
    FinalizeSource,
    TakeConstants,
    TakeContext,
    build_plan,
    finalize_request,
    missions_payload,
    plan_request,
)
from pitch_coach_backend.core.database import SessionPerCall
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.dto import StoredEvent, TakeCoachValues


class DbCoachStore(SessionPerCall):
    async def append_events(self, take_id: uuid.UUID, events: Sequence[StoredEvent]) -> None:
        await run_in_threadpool(self._with, coach_service.append_coach_events, take_id, events)

    async def load_events(self, take_id: uuid.UUID) -> list[dict[str, Any]] | None:
        return await run_in_threadpool(self._with, coach_service.load_coach_events, take_id)

    async def load_finalize_source(self, take_id: uuid.UUID) -> FinalizeSource | None:
        return await run_in_threadpool(self._with, load_finalize_source, take_id)

    async def save_take_result(
        self, take_id: uuid.UUID, reply: FinalizeReply, events: Sequence[StoredEvent]
    ) -> bool:
        return await run_in_threadpool(self._with, save_take_result, take_id, reply, events)

    async def fail_take_result(self, take_id: uuid.UUID) -> bool:
        return await run_in_threadpool(self._with, coach_service.fail_take_result, take_id)

    async def finalizing_takes(
        self,
        stale_before: datetime | None = None,
        *,
        after: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[uuid.UUID]:
        return await run_in_threadpool(
            self._with,
            lambda db: coach_service.finalizing_takes(db, stale_before, after=after, limit=limit),
        )

    async def purge_expired_raw(self, now: datetime) -> int:
        return await run_in_threadpool(self._with, coach_service.purge_expired_raw, now)

    async def build_plan_request(self, pitch_id: uuid.UUID) -> dict[str, Any] | None:
        return await run_in_threadpool(self._with, build_plan_request, pitch_id)

    async def save_plan(
        self,
        pitch_id: uuid.UUID,
        request: Mapping[str, Any],
        reply: PlanReply,
        requested_at: datetime,
    ) -> bool:
        return await run_in_threadpool(
            self._with, save_coaching_plan, pitch_id, request, reply, requested_at
        )


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

    async def finalizing_takes(
        self,
        stale_before: datetime | None = None,
        *,
        after: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[uuid.UUID]:
        """Take 결과를 기다리는 Take 를 ID 오름차순으로 `after` 뒤에서 `limit` 개 읽는다.
        `stale_before` 를 주면 그 전에 기다리기 시작한 것만 읽는다."""
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


def load_finalize_source(
    db: Session, take_id: uuid.UUID, *, waiting_only: bool = True
) -> FinalizeSource | None:
    """finalize 를 DB 에서 다시 만들 재료. Take 가 Take 결과를 기다리는 중이 아니면 None."""
    rows = coach_service.finalize_rows(db, take_id, waiting_only=waiting_only)
    if rows is None:
        return None
    return FinalizeSource(
        context=_take_context(db, rows.values),
        seconds=rows.seconds,
        segments=rows.segments,
        utterance_ends=rows.utterance_ends,
        events=rows.events,
        raw_data_consent=rows.raw_data_consent,
    )


def recovery_request(db: Session, take_id: uuid.UUID) -> dict[str, Any] | None:
    source = load_finalize_source(db, take_id, waiting_only=False)
    if source is None or (t_end_ms := source.t_end_ms) is None:
        return None
    replay = source.replay(t_end_ms)
    return finalize_request(
        source.context.constants,
        t_end_ms,
        source.last_window(replay, t_end_ms),
        None,
        source.events,
        replay=replay,
    )


class StoreFinalizeExport:
    def recovery_request(self, db: Session, take_id: uuid.UUID) -> dict[str, Any] | None:
        return recovery_request(db, take_id)


def save_take_result(
    db: Session, take_id: uuid.UUID, reply: FinalizeReply, events: Sequence[StoredEvent]
) -> bool:
    """finalize 응답을 저장한다 (`coach_service.save_take_result`). 발표 중 못 넣은 이벤트 뒤에
    이번 응답의 이벤트를 붙인다. 저장했으면 True."""
    return coach_service.save_take_result(
        db,
        take_id,
        take_result=reply.take_result,
        meta=reply.meta,
        events=[*events, *((event, reply.meta) for event in reply.events)],
        measures=asdict(reply.summary()),
        duration_sec=reply.duration_sec,
    )


def build_plan_request(db: Session, pitch_id: uuid.UUID) -> dict[str, Any] | None:
    """다음 Take 의 `/coach/plan` 요청 (coach-agent.md 5-2 plan). 부르지 않을 때는 None
    (`coach_service.coaching_plan_values`, 입력 상한은 `plan_request`)."""
    values = coach_service.coaching_plan_values(db, pitch_id)
    if values is None:
        return None
    return plan_request(
        pitch_id=pitch_id,
        source_take_id=values.source_take_id,
        script_version_id=values.script_version_id,
        plan=build_plan(
            values.time_limit_sec,
            time_limit_sec=values.time_limit_sec,
            lower_deviation_sec=values.lower_deviation_sec,
            upper_deviation_sec=values.upper_deviation_sec,
            slides=values.slides,
            parsed_target_ms=values.parsed_target_ms,
        ),
        scripts=values.scripts,
        missions=values.missions,
        recurring_issues=values.recurring_issues,
        previous_take_result=values.previous_take_result,
        previous_review=values.previous_review,
    )


def save_coaching_plan(
    db: Session,
    pitch_id: uuid.UUID,
    request: Mapping[str, Any],
    reply: PlanReply,
    requested_at: datetime,
) -> bool:
    """계획 응답을 한 행으로 더한다 (기본 계획도). 발표가 그 사이 지워졌으면 넣지 않고 False."""
    basis = request_basis(request)
    return coach_service.save_coaching_plan(
        db,
        pitch_id,
        source_take_id=_uuid(basis.source_take_id),
        script_version_id=_uuid(basis.script_version_id),
        target_ms=basis.target_ms,
        min_ms=basis.min_ms,
        max_ms=basis.max_ms,
        payload=plan_payload(request, reply),
        meta=dict(reply.meta),
        requested_at=requested_at,
    )


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value is not None else None
