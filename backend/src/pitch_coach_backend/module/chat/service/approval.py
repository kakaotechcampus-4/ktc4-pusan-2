"""확인 카드의 승인 · 거절(chat-agent.md 12-3 '승인 · 거절', 실패 표 L2-40 ~ L2-43).

화면은 action_id · 결정 · 화면에 보인 args_hash 만 보낸다. 승인하면 저장한 인자로만 실행하도록
행동에 표시하고 실행을 RUNNING 으로 돌린 뒤 턴 실행기(turn_runner.resume)가 이어 간다.
거절은 AI 를 부르지 않고 이미 낸 문장 뒤에 고정 문장을 덧붙여 끝낸다.
같은 action_id 로 두 번 오면 처음 결과를 돌려준다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import RunStatusResponse
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatAction, ChatRun
from pitch_coach_backend.module.chat.exception import (
    ChatCancelled,
    ChatCardChanged,
    ChatExpired,
    ChatTargetNotFound,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.run import take_lease
from pitch_coach_backend.module.chat.service.run_view import view_of
from pitch_coach_backend.module.chat.service.waiting import (
    EXPIRED_TEXT,
    NOT_DONE_TEXT,
    Emitted,
    close_waiting,
    is_overdue,
    read_emitted,
)

Decision = Literal["APPROVE", "REJECT"]


@dataclass(frozen=True)
class Decided:
    view: RunStatusResponse
    # 턴 실행기를 이어 돌릴지. 승인을 커밋한 뒤에 부른다
    resume: bool


def decide_action(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    action_id: str,
    decision: Decision,
    args_hash: str,
    now: datetime,
) -> Decided:
    try:
        return _decide(db, user_id, run_id, action_id, decision, args_hash, now)
    except Exception:
        db.rollback()
        raise


def _decide(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    action_id: str,
    decision: Decision,
    args_hash: str,
    now: datetime,
) -> Decided:
    repo = ChatApiRepository(db)
    action = ChatRepository(db).get_action(run_id, action_id)
    run = repo.get_owned_run(run_id, user_id)
    if run is None or action is None or action.autonomy != "ASK":
        raise ChatTargetNotFound()
    # 이미 결정했으면 잠그지 않고 처음 결과를 준다. 이어 돌던 턴 실행기와 잠금을 다투지 않는다
    if action.approved is not None:
        return _first_result(db, run, action)
    emitted = read_emitted(db, run.id)
    if repo.lock_conversation_of(run) is None:
        raise ChatTargetNotFound()
    db.refresh(run, with_for_update=True)
    db.refresh(action, with_for_update=True)
    # 잠금을 기다리는 사이 다른 요청이 결정했을 수 있다
    if action.approved is not None:
        return _first_result(db, run, action)
    if run.status == "EXPIRED" or action.status == "EXPIRED":
        raise ChatExpired()
    if run.status != "WAITING_APPROVAL" or action.status != "PENDING":
        raise ChatCancelled()
    if action.args_hash != args_hash:
        raise ChatCardChanged()
    if is_overdue(run, now):
        _expire(db, run, action, emitted, now)
        raise ChatExpired()
    if decision == "REJECT":
        close_waiting(
            db,
            run,
            action,
            emitted,
            action_status="REJECTED",
            run_status="DONE",
            text=NOT_DONE_TEXT[action.type],
            now=now,
        )
        db.commit()
        return Decided(view_of(db, run), resume=False)
    _approve(run, action, now)
    db.commit()
    return Decided(view_of(db, run), resume=True)


def _approve(run: ChatRun, action: ChatAction, now: datetime) -> None:
    # 행동은 PENDING 그대로 둔다.
    # 턴 실행기가 승인된 PENDING 행동을 찾아 실행하며 RUNNING 으로 바꾼다
    action.approved = True
    action.approved_at = now
    # 사용자를 기다린 시간은 실행의 120초에 넣지 않는다(12-3 '만료 · 시간 초과')
    run.deadline_at += now - run.approval_started_at
    run.approval_ended_at = now
    run.status = "RUNNING"
    # 대기하는 동안 임대가 지났다. 그대로 RUNNING 이 되면 턴 실행기가 잡기 전에
    # 재시작 복구(run_recovery.py)가 죽은 실행으로 보고 가져간다
    take_lease(run)
    run.row_revision += 1


def _expire(db: Session, run: ChatRun, action: ChatAction, emitted: Emitted, now: datetime) -> None:
    close_waiting(
        db,
        run,
        action,
        emitted,
        action_status="EXPIRED",
        run_status="EXPIRED",
        text=EXPIRED_TEXT,
        now=now,
    )
    db.commit()


def _first_result(db: Session, run: ChatRun, action: ChatAction) -> Decided:
    # 승인을 커밋한 뒤 응답을 잃어 턴 실행기를 못 띄웠으면 다시 보낸 요청이 띄운다.
    # 턴 실행기는 승인된 PENDING 행동만 잡으므로 두 번 띄워도 한 번만 돈다
    resume = action.approved is True and action.status == "PENDING" and run.status == "RUNNING"
    return Decided(view_of(db, run), resume=resume)
