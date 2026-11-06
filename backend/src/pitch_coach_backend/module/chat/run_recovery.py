import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.goal_sweep import _in_session
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service import turn as turn_service

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 15.0


def _unfinished_run_ids(db: Session) -> list[uuid.UUID]:
    return ChatRepository(db).list_unfinished_run_ids()


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    now = datetime.now(UTC)
    try:
        run_ids = await run_in_threadpool(_in_session, session_factory, _unfinished_run_ids)
    except Exception:
        logger.exception("끝나지 않은 대화 실행을 읽지 못했다. 다음 주기에 다시 한다")
        return 0
    started = 0
    for run_id in run_ids:
        try:
            step = await run_in_threadpool(
                _in_session, session_factory, turn_service.recover_run, run_id, now
            )
        except Exception:
            logger.exception("대화 실행을 복구하지 못했다 run=%s", run_id)
            continue
        if step == "START":
            turn_runner.start(run_id, session_factory)
        elif step == "RESUME":
            turn_runner.recover(run_id, session_factory)
        else:
            continue
        started += 1
    if started:
        logger.info("대화 실행 %d개를 복구해 다시 띄웠다", started)
    return started


async def run_forever(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    while True:
        await sweep_once()
        await asyncio.sleep(interval_sec)


def start() -> Callable[[], Awaitable[None]]:
    task = asyncio.create_task(run_forever(), name="chat-run-recovery")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
