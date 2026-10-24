"""시작하지 않은 채 남은 Take 를 끝내는 주기 작업. main.py 의 lifespan 이 띄운다.

FE 는 첫 실시간 연결이 `ready` 를 받은 뒤에 발표를 시작하고, 그 연결이 Take 를 `RUNNING` 으로
바꾼다. 그래서 만든 지 오래된 `READY` 는 장치 점검에서 시작을 누른 뒤 연결이 끝내 붙지 않은 Take 다
(`service.READY_EXPIRE_AFTER`). 같은 발표에서 새 Take 를 만들 때도 끝내지만(`abandon_ready_takes`),
다시 연습하지 않으면 그대로 남는다.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.take import service

logger = logging.getLogger(__name__)

# 정리하는 간격. 기준(10분)보다 짧으면 된다
SWEEP_INTERVAL_SEC = 60.0


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    def sweep() -> int:
        db = session_factory()
        try:
            return service.abandon_stale_ready_takes(db)
        finally:
            db.close()

    try:
        abandoned = await run_in_threadpool(sweep)
    except Exception:
        logger.exception("시작하지 않은 Take 를 끝내지 못했다. 다음 주기에 다시 한다")
        return 0
    if abandoned:
        logger.info("시작하지 않은 Take %d개를 ABANDONED 로 끝냈다", abandoned)
    return abandoned


async def run_forever(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    while True:
        await sweep_once()
        await asyncio.sleep(interval_sec)


def start() -> Callable[[], Awaitable[None]]:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    task = asyncio.create_task(run_forever(), name="take-ready-sweep")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
