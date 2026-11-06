import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import Stop, start_periodic
from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.service import goal_check as goal_check_service
from pitch_coach_backend.module.chat.service.run import OWNER

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 60.0


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    try:
        now = datetime.now(UTC)
        await in_session(session_factory, goal_check_service.deactivate_goals_without_base)
        targets = await in_session(session_factory, goal_check_service.pick_targets, now)
        started = 0
        for target in targets:
            run_id = await in_session(
                session_factory, goal_check_service.claim_target, target, OWNER, now
            )
            if run_id is not None:
                turn_runner.start(run_id, session_factory)
                started += 1
    except Exception:
        logger.exception("자동 목표 확인을 하지 못했다. 다음 주기에 다시 한다")
        return 0
    return started


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_periodic(sweep_once, SWEEP_INTERVAL_SEC, name="chat-goal-sweep")
