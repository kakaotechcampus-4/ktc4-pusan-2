import asyncio
import contextlib
import logging
import uuid
import weakref
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.take.service import review as review_service

logger = logging.getLogger(__name__)

ANALYSIS_CONCURRENCY = 4
CONFIRMATION_WAIT = timedelta(minutes=10)
RESUME_BATCH_SIZE = 4
RESUME_READ_TIMEOUT_SEC = 10.0
SWEEP_INTERVAL_SEC = 5 * 60.0
STALE_ANALYSIS = timedelta(minutes=10)
STOP_TIMEOUT_SEC = 5.0

type SessionFactory = Callable[[], Session]

_running = TaskRegistry()
_recovery = TaskRegistry()
_slots_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def start(
    take_id: uuid.UUID, session_factory: SessionFactory = SessionLocal
) -> asyncio.Task[None] | None:
    if _running.running(take_id):
        return None
    return _running.spawn(
        run(take_id, session_factory), name=f"take-analysis:{take_id}", key=take_id
    )


async def run(take_id: uuid.UUID, session_factory: SessionFactory = SessionLocal) -> None:
    try:
        await _run(take_id, session_factory)
    except asyncio.CancelledError:
        logger.info("Take 분석을 멈춘다 (앱 종료) take=%s", take_id)
        raise
    except Exception:
        logger.exception("Take 분석 실패 take=%s", take_id)


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (slots := _slots_by_loop.get(loop)) is None:
        slots = asyncio.Semaphore(ANALYSIS_CONCURRENCY)
        _slots_by_loop[loop] = slots
    return slots


async def _in_session[T](
    session_factory: SessionFactory, fn: Callable[..., T], *args: Any
) -> T:
    def call() -> T:
        db = session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await run_in_threadpool(call)


async def _run(take_id: uuid.UUID, session_factory: SessionFactory) -> None:
    async with _slots():
        outcome = await coverage_service.analyze_script_coverage(take_id, session_factory)
    if outcome == "HELD":
        logger.warning("대본 전달도 병합이 실패해 리뷰를 보류한다 take=%s", take_id)
        return
    await _wait_for_confirmation(take_id, session_factory)
    pitch_id = await _review(take_id, session_factory)
    if pitch_id is not None:
        _plan_next_take(pitch_id)


def _confirmation_started_at(db: Session, take_id: uuid.UUID) -> datetime | None:
    take = TakeRepository(db).get_take(take_id)
    if take is None or take.status != "ANALYZING" or take.analysis_stage != "AWAITING_CONFIRMATION":
        return None
    return take.analysis_stage_at


async def _wait_for_confirmation(take_id: uuid.UUID, session_factory: SessionFactory) -> None:
    started_at = await _in_session(session_factory, _confirmation_started_at, take_id)
    if started_at is None:
        return
    delay = (started_at + CONFIRMATION_WAIT - datetime.now(UTC)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


async def _review(take_id: uuid.UUID, session_factory: SessionFactory) -> uuid.UUID | None:
    request_id = uuid.uuid4()
    async with _slots():
        pitch_id = await _in_session(
            session_factory, review_service.claim_review, take_id, request_id
        )
        if pitch_id is None:
            return None
        try:
            review_request = await _in_session(
                session_factory, review_service.build_review_request, take_id
            )
            reply = await review_service.request_review_async(take_id, review_request)
            saved = await _in_session(
                session_factory,
                partial(review_service.save_review, request_id=request_id),
                take_id,
                reply,
            )
        except Exception:
            logger.exception("리뷰 실패 take=%s", take_id)
            failed = await _in_session(
                session_factory, review_service.fail_review, take_id, request_id
            )
            return pitch_id if failed else None
    if not saved:
        logger.info("리뷰 결과를 버렸다 (다른 작업이 이어받았거나 지워짐) take=%s", take_id)
        return None
    return pitch_id


def _plan_next_take(pitch_id: uuid.UUID) -> None:
    try:
        hooks.coaching().plan_next_take(pitch_id)
    except Exception:
        logger.exception("리뷰 뒤 코칭 계획을 띄우지 못했다 pitch=%s", pitch_id)


def resume(
    session_factory: SessionFactory = SessionLocal, stale_before: datetime | None = None
) -> None:
    if not _recovery.running("resume"):
        _recovery.spawn(
            _resume(session_factory, stale_before), name="take-analysis-resume", key="resume"
        )


def _analyzing_takes(
    db: Session, stale_before: datetime | None, after: uuid.UUID | None
) -> list[uuid.UUID]:
    return TakeRepository(db).analyzing_takes(stale_before, after=after, limit=RESUME_BATCH_SIZE)


async def _resume(session_factory: SessionFactory, stale_before: datetime | None) -> None:
    after: uuid.UUID | None = None
    resumed = 0
    try:
        while True:
            take_ids = await asyncio.wait_for(
                _in_session(session_factory, _analyzing_takes, stale_before, after),
                RESUME_READ_TIMEOUT_SEC,
            )
            for take_id in take_ids:
                if start(take_id, session_factory) is not None:
                    resumed += 1
            if len(take_ids) < RESUME_BATCH_SIZE:
                break
            after = take_ids[-1]
    except Exception:
        logger.exception("분석을 이어 갈 Take 를 읽지 못했다")
    if resumed:
        logger.info("분석 중이던 Take %d개를 다시 띄웠다", resumed)


async def sweep_forever(
    session_factory: SessionFactory = SessionLocal, interval_sec: float = SWEEP_INTERVAL_SEC
) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        resume(session_factory, stale_before=datetime.now(UTC) - STALE_ANALYSIS)


async def stop_all(timeout: float = STOP_TIMEOUT_SEC) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    await _recovery.stop_all(timeout)
    await _running.stop_all(max(0.0, deadline - asyncio.get_running_loop().time()))


def start_background() -> Callable[[], Awaitable[None]]:
    resume()
    sweep = asyncio.create_task(sweep_forever(), name="take-analysis-sweep")

    async def stop() -> None:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep
        await stop_all()

    return stop
