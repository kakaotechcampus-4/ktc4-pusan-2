"""S3 에 남은 발표 파일을 다시 지우는 주기 작업. main.py 의 lifespan 이 띄운다.

발표를 지운 뒤 응답 뒤 백그라운드 삭제가 실패하거나 프로세스가 중간에 끊기면 지울 경로가 DB 에
남는다 (`service/file_cleanup.py`). 뜰 때 한 번 하고 주기마다 남은 것을 지운다.
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.tasks import Stop, start_periodic
from pitch_coach_backend.module.pitch.service import file_cleanup

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 300.0


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    try:
        deleted = await run_in_threadpool(file_cleanup.retry_pending, session_factory)
    except Exception:
        logger.exception("남은 발표 파일을 지우지 못했다. 다음 주기에 다시 한다")
        return 0
    if deleted:
        logger.info("남아 있던 발표 파일 경로 %d개를 지웠다", deleted)
    return deleted


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_periodic(sweep_once, SWEEP_INTERVAL_SEC, name="pitch-file-cleanup-sweep")
