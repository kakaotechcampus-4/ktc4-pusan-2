"""30분이 지난 승인 대기를 끝내는 주기 작업(chat-agent.md 12-3 '만료 · 시간 초과').

AI 를 부르지 않으므로 대화 스위치가 꺼져 있어도 돈다.
조건을 잠그고 다시 보므로 겹쳐 돌아도 한 번만 끝낸다.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.chat.service import waiting

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 60.0


def _in_session[T](session_factory: Callable[[], Session], fn: Callable[..., T], *args: Any) -> T:
    db = session_factory()
    try:
        return fn(db, *args)
    finally:
        db.close()


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    expired = 0
    try:
        now = datetime.now(UTC)
        run_ids = await run_in_threadpool(
            _in_session, session_factory, waiting.overdue_run_ids, now
        )
    except Exception:
        logger.exception("승인 대기 만료를 찾지 못했다. 다음 주기에 다시 한다")
        return 0
    for run_id in run_ids:
        try:
            if await run_in_threadpool(
                _in_session, session_factory, waiting.expire_run, run_id, now
            ):
                expired += 1
        except Exception:
            # 한 건이 실패해도 나머지는 끝낸다. 남은 건은 다음 주기에 다시 고른다
            logger.exception("승인 대기를 만료하지 못했다 run=%s", run_id)
    return expired


async def run_forever(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    while True:
        await sweep_once()
        await asyncio.sleep(interval_sec)


def start() -> Callable[[], Awaitable[None]]:
    task = asyncio.create_task(run_forever(), name="chat-approval-sweep")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
