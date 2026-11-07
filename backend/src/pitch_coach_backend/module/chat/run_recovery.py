import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import Stop, start_periodic
from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service import turn as turn_service

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 15.0


def _unfinished_run_ids(db: Session) -> list[uuid.UUID]:
    return ChatRepository(db).list_unfinished_run_ids()


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    now = datetime.now(UTC)
    try:
        run_ids = await in_session(session_factory, _unfinished_run_ids)
    except Exception:
        logger.exception("끝나지 않은 대화 실행을 읽지 못했다. 다음 주기에 다시 한다")
        return 0
    started = 0
    for run_id in run_ids:
        try:
            step = await in_session(session_factory, turn_service.recover_run, run_id, now)
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


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_periodic(sweep_once, SWEEP_INTERVAL_SEC, name="chat-run-recovery")
