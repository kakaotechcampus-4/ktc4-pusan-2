"""30분이 지난 승인 대기를 끝내는 주기 작업(chat-agent.md 12-3 '만료 · 시간 초과').

AI 를 부르지 않으므로 대화 스위치가 꺼져 있어도 돈다.
조건을 잠그고 다시 보므로 겹쳐 돌아도 한 번만 끝낸다.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import Stop, start_periodic
from pitch_coach_backend.module.chat.service import waiting

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SEC = 60.0


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    expired = 0
    try:
        now = datetime.now(UTC)
        run_ids = await in_session(session_factory, waiting.overdue_run_ids, now)
    except Exception:
        logger.exception("승인 대기 만료를 찾지 못했다. 다음 주기에 다시 한다")
        return 0
    for run_id in run_ids:
        try:
            if await in_session(session_factory, waiting.expire_run, run_id, now):
                expired += 1
        except Exception:
            # 한 건이 실패해도 나머지는 끝낸다. 남은 건은 다음 주기에 다시 고른다
            logger.exception("승인 대기를 만료하지 못했다 run=%s", run_id)
    return expired


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_periodic(sweep_once, SWEEP_INTERVAL_SEC, name="chat-approval-sweep")
