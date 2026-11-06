import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.chat.goal_sweep import OWNER, _in_session
from pitch_coach_backend.module.chat.service import cleanup as cleanup_service

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 3600.0
ALERT_FAILURES = 2


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> bool:
    now = datetime.now(UTC)
    try:
        if await run_in_threadpool(
            _in_session, session_factory, cleanup_service.cleanup_delayed, now
        ):
            logger.error("대화 정리 작업이 2시간 넘게 끝나지 않았다")
        cleaned = await run_in_threadpool(
            _in_session, session_factory, cleanup_service.run_cleanup, OWNER, now
        )
    except Exception:
        logger.exception("대화 정리 작업이 실패했다. 다음 주기에 다시 한다")
        return False
    if cleaned:
        logger.info("지워진 근거에서 나온 대화 사본 %d개를 정리했다", cleaned)
    return True


async def run_forever(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    failures = 0
    while True:
        failures = 0 if await sweep_once() else failures + 1
        if failures >= ALERT_FAILURES:
            logger.error("대화 정리 작업이 %d번 연속 실패했다", failures)
        await asyncio.sleep(interval_sec)


def start() -> Callable[[], Awaitable[None]]:
    task = asyncio.create_task(run_forever(), name="chat-cleanup-sweep")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
