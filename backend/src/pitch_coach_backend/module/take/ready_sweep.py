"""시작하지 않은 채 남은 Take 를 끝내는 주기 작업. main.py 의 lifespan 이 띄운다.

FE 는 첫 실시간 연결이 `ready` 를 받은 뒤에 발표를 시작하고, 그 연결이 Take 를 `RUNNING` 으로
바꾼다. 그래서 만든 지 오래된 `READY` 는 장치 점검에서 시작을 누른 뒤 연결이 끝내 붙지 않은 Take 다
(`service/lifecycle.py` 의 `READY_EXPIRE_AFTER`). 같은 발표에서 새 Take 를 만들 때도
끝내지만(`abandon_ready_takes`), 다시 연습하지 않으면 그대로 남는다.
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import Stop, start_periodic
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service

logger = logging.getLogger(__name__)

# 정리하는 간격. 기준(10분)보다 짧으면 된다
SWEEP_INTERVAL_SEC = 60.0


async def sweep_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    try:
        abandoned = await in_session(session_factory, lifecycle_service.abandon_stale_ready_takes)
    except Exception:
        logger.exception("시작하지 않은 Take 를 끝내지 못했다. 다음 주기에 다시 한다")
        return 0
    if abandoned:
        logger.info("시작하지 않은 Take %d개를 ABANDONED 로 끝냈다", abandoned)
    return abandoned


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_periodic(sweep_once, SWEEP_INTERVAL_SEC, name="take-ready-sweep")
