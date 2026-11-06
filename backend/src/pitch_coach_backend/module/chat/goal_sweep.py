import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.service import goal_check as goal_check_service
from pitch_coach_backend.module.chat.service.run import OWNER

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 60.0


def _in_session[T](session_factory: Callable[[], Session], fn: Callable[..., T], *args: Any) -> T:
    db = session_factory()
    try:
        return fn(db, *args)
    finally:
        db.close()


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    try:
        now = datetime.now(UTC)
        await run_in_threadpool(
            _in_session, session_factory, goal_check_service.deactivate_goals_without_base
        )
        targets = await run_in_threadpool(
            _in_session, session_factory, goal_check_service.pick_targets, now
        )
        started = 0
        for target in targets:
            run_id = await run_in_threadpool(
                _in_session, session_factory, goal_check_service.claim_target, target, OWNER, now
            )
            if run_id is not None:
                turn_runner.start(run_id, session_factory)
                started += 1
    except Exception:
        logger.exception("자동 목표 확인을 하지 못했다. 다음 주기에 다시 한다")
        return 0
    return started


async def run_forever(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    while True:
        await sweep_once()
        await asyncio.sleep(interval_sec)


def start() -> Callable[[], Awaitable[None]]:
    task = asyncio.create_task(run_forever(), name="chat-goal-sweep")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
