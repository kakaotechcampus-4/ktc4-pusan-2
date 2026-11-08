"""대화 스위치를 끈 채 앱이 뜰 때 한 번 도는 정리 작업(chat-agent.md 11-2).

운영은 재시작으로만 스위치를 바꾼다. 꺼진 채 뜨면 재시작 복구가 뜨지 않아 끝나지 않은 실행이
그대로 남으므로 여기서 CANCELLED 로 끝낸다. 스위치가 꺼져 있으면 재시작 복구 · 자동 목표 확인은
뜨지 않고, 승인 대기 만료와는 둘 다 실행을 잠그고 상태를 다시 보므로 겹쳐도 한 번만 끝낸다.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import Stop, start_task
from pitch_coach_backend.module.chat.service import switch_off

logger = logging.getLogger(__name__)


async def close_once(session_factory: Callable[[], Session] = SessionLocal) -> int:
    try:
        run_ids = await in_session(session_factory, switch_off.active_run_ids)
    except Exception:
        logger.exception("끝나지 않은 대화 실행을 읽지 못했다. 다음에 뜰 때 다시 한다")
        return 0
    cancelled = 0
    for run_id in run_ids:
        # 실행마다 짧은 트랜잭션으로 끝낸다. 한 건이 실패해도 나머지는 끝낸다.
        # 시각도 실행마다 잡는다. 실행이 많으면 뒤쪽 실행의 끝난 시각 · 걸린 시간이 실제보다 이르다
        now = datetime.now(UTC)
        try:
            if await in_session(session_factory, switch_off.cancel_active_run, run_id, now):
                cancelled += 1
        except Exception:
            logger.exception("대화 실행을 취소하지 못했다 run=%s", run_id)
    if cancelled:
        logger.info("대화 스위치가 꺼져 있어 실행 %d개를 취소했다", cancelled)
    return cancelled


async def _close() -> None:
    await close_once()


def start() -> Stop:
    """작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다. 끝나기 전에 내리면 남은 실행은 다음에 뜰 때
    다시 고른다."""
    return start_task(_close(), name="chat-switch-off-sweep")
