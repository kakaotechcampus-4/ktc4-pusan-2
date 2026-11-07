"""대화 스위치를 끈 채 앱이 뜰 때 끝나지 않은 실행을 CANCELLED 로 끝낸다(chat-agent.md 11-2).

이미 커밋한 행동은 그대로 둔다. 화면의 실행 취소(run_view.py `cancel_run`)와 같은 일을 한다.
AI 를 부르지 않는다.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.dto import AgentTrigger
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import close_goal_lease
from pitch_coach_backend.module.chat.service.run import mark_cancelled
from pitch_coach_backend.module.chat.service.waiting import (
    CANCELLED_TEXT,
    close_waiting,
    read_emitted,
)


def active_run_ids(db: Session) -> list[uuid.UUID]:
    return ChatApiRepository(db).active_run_ids()


def cancel_active_run(db: Session, run_id: uuid.UUID, now: datetime) -> bool:
    """실행 하나를 CANCELLED 로 끝낸다. 그사이 끝났으면 건드리지 않고 False."""
    repo = ChatRepository(db)
    row = repo.lock_run(run_id, statuses=("QUEUED", "RUNNING"))
    if row is None:
        # 승인 대기는 대화부터 잠가야 해서 이 잠금을 풀고 따로 한다
        db.rollback()
        return _cancel_waiting(db, run_id, now)
    if row.first_trigger == AgentTrigger.TAKE_REVIEWED:
        # 임대가 PENDING 으로 남으면 스위치를 켠 뒤 같은 리뷰로 실행을 다시 만든다
        close_goal_lease(repo, row)
    mark_cancelled(repo, row, now)
    db.commit()
    return True


def _cancel_waiting(db: Session, run_id: uuid.UUID, now: datetime) -> bool:
    """확정 전 행동까지 끝내고 고정 문장을 남긴다. 잠금 순서는 waiting.py `expire_run` 과 같다."""
    try:
        emitted = read_emitted(db, run_id)
        repo = ChatApiRepository(db)
        run = db.get(ChatRun, run_id)
        if run is None or repo.lock_conversation_of(run) is None:
            db.rollback()
            return False
        db.refresh(run, with_for_update=True)
        if run.status != "WAITING_APPROVAL":
            db.rollback()
            return False
        close_waiting(
            db,
            run,
            repo.waiting_action(run.id),
            emitted,
            action_status="CANCELLED",
            run_status="CANCELLED",
            text=CANCELLED_TEXT,
            now=now,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True
