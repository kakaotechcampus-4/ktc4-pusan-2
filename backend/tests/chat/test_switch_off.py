"""대화 스위치가 꺼진 채 앱이 뜨면 끝나지 않은 실행을 CANCELLED 로 정리한다(chat-agent.md 11-2)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import switch_off_sweep
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatLease,
    ChatMessage,
    ChatRun,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.service.run import SEOUL
from pitch_coach_backend.module.chat.service.waiting import CANCELLED_TEXT
from tests.chat.test_chat_approval import CLAIM, _answers, _waiting
from tests.chat.test_turn_runner import session_factory  # noqa: F401

SENT = [{"turn_call": 1, "request_id": "r1", "state_revision": None, "stage": "DONE"}]


def _run(db_session: Session, user_id: uuid.UUID, **fields: Any) -> ChatRun:
    """대화마다 끝나지 않은 실행은 하나라 실행마다 대화를 만든다."""
    conversation = ChatConversation(user_id=user_id, title="질문")
    db_session.add(conversation)
    db_session.flush()
    now = datetime.now(UTC)
    values = {
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "first_trigger": "USER_MESSAGE",
        "status": "RUNNING",
        "input": {"user_message": "어땠어?", "page": None, "choice": None},
        "accepted_at": now - timedelta(seconds=10),
        "deadline_at": now + timedelta(seconds=110),
        **fields,
    }
    row = ChatRun(**values)
    db_session.add(row)
    # 정리 작업은 실행마다 따로 커밋 · rollback 한다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()
    return row


def _close(session_factory) -> int:  # noqa: F811
    return asyncio.run(switch_off_sweep.close_once(session_factory))


def _reload(db_session: Session, row: Any) -> Any:
    db_session.expire_all()
    return db_session.get(type(row), row.id)


def test_queued_and_running_runs_are_cancelled(db_session, session_factory, user_id):  # noqa: F811
    queued = _run(db_session, user_id, status="QUEUED")
    running = _run(db_session, user_id, turn_calls=SENT, state={"v": 1})
    revisions = (queued.row_revision, running.row_revision)

    assert _close(session_factory) == 2

    queued, running = _reload(db_session, queued), _reload(db_session, running)
    assert [(run.status, run.error_code) for run in (queued, running)] == [
        ("CANCELLED", "AGENT_CANCELLED"),
        ("CANCELLED", "AGENT_CANCELLED"),
    ]
    # 판 번호를 올려 늦게 도착한 턴 실행기의 저장이 반영되지 않게 한다
    assert (queued.row_revision, running.row_revision) == (revisions[0] + 1, revisions[1] + 1)
    assert running.state is None
    logged = db_session.scalars(
        select(ChatRunLog.status).where(ChatRunLog.run_id.in_([queued.id, running.id]))
    ).all()
    assert logged == ["CANCELLED", "CANCELLED"]


def test_unsent_run_returns_its_service_reservation(db_session, session_factory, user_id):  # noqa: F811
    unsent = _run(db_session, user_id, status="QUEUED")
    _run(db_session, user_id, turn_calls=SENT)
    day = unsent.accepted_at.astimezone(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=None, day=day, reserved=1))
    db_session.commit()

    _close(session_factory)

    db_session.expire_all()
    usage = db_session.scalar(
        select(ChatDailyUsage).where(ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day)
    )
    # AI 에 보내지 않은 실행의 예약만 돌려준다
    assert usage.reserved == 0


def test_auto_goal_check_closes_its_lease(db_session, session_factory, user_id):  # noqa: F811
    run = _run(
        db_session,
        user_id,
        first_trigger="TAKE_REVIEWED",
        status="QUEUED",
        input={"review_event": {}, "page": None},
        fencing_token=1,
    )
    lease = ChatLease(
        kind="GOAL_CHECK",
        conversation_id=run.conversation_id,
        goal_version=1,
        take_id=uuid.uuid4(),
        review_revision=1,
        status="PENDING",
        run_id=run.id,
        owner="sweep",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        fencing_token=1,
    )
    db_session.add(lease)
    db_session.commit()

    _close(session_factory)

    assert _reload(db_session, run).status == "CANCELLED"
    # PENDING 으로 남으면 스위치를 켠 뒤 같은 리뷰로 실행을 다시 만들고 확인 횟수를 또 줄인다
    assert _reload(db_session, lease).status == "DONE"


def test_waiting_run_cancels_its_action_with_the_fixed_text(
    db_session,
    session_factory,  # noqa: F811
    user_id: uuid.UUID,
):
    seeded = _waiting(db_session, user_id)

    assert _close(session_factory) == 1

    run = _reload(db_session, seeded.run)
    assert (run.status, run.error_code, run.state) == ("CANCELLED", "AGENT_CANCELLED", None)
    assert _reload(db_session, seeded.action).status == "CANCELLED"
    [answer] = _answers(db_session, seeded.conversation.id)
    assert answer.text == f"{CLAIM['text']}\n{CANCELLED_TEXT}"
    assert answer.status == "PARTIAL"


def test_finished_runs_and_committed_actions_are_left_alone(
    db_session,
    session_factory,  # noqa: F811
    user_id: uuid.UUID,
):
    done = _run(db_session, user_id, status="DONE", finished_at=datetime.now(UTC))
    failed = _run(db_session, user_id, status="FAILED", error_code="AGENT_TIMEOUT")
    running = _run(db_session, user_id, turn_calls=SENT)
    committed = ChatAction(
        conversation_id=running.conversation_id,
        user_id=user_id,
        run_id=running.id,
        action_id="a-1",
        type="FETCH_TAKE",
        args={},
        args_digest="d",
        autonomy="AUTO",
        idempotency_key=f"{running.id}:1",
        args_hash="sha256:" + "0" * 64,
        status="SUCCEEDED",
        result={"status": "SUCCEEDED"},
    )
    db_session.add(committed)
    db_session.commit()
    revisions = (done.row_revision, failed.row_revision)

    assert _close(session_factory) == 1

    done, failed = _reload(db_session, done), _reload(db_session, failed)
    assert [(run.status, run.row_revision) for run in (done, failed)] == [
        ("DONE", revisions[0]),
        ("FAILED", revisions[1]),
    ]
    assert _reload(db_session, committed).status == "SUCCEEDED"
    # 다시 돌아도 끝난 실행은 건드리지 않는다
    assert _close(session_factory) == 0


def test_one_failing_run_does_not_stop_the_rest(
    db_session,
    session_factory,  # noqa: F811
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    broken = _run(db_session, user_id, status="QUEUED")
    other = _run(db_session, user_id, status="QUEUED")
    real = switch_off_sweep.switch_off.cancel_active_run

    def cancel(db: Session, run_id: uuid.UUID, now: datetime) -> bool:
        if run_id == broken.id:
            raise RuntimeError("잠금 대기 초과")
        return real(db, run_id, now)

    monkeypatch.setattr(switch_off_sweep.switch_off, "cancel_active_run", cancel)

    assert _close(session_factory) == 1

    assert _reload(db_session, broken).status == "QUEUED"
    assert _reload(db_session, other).status == "CANCELLED"


@pytest.mark.parametrize("enabled", [True, False])
def test_closing_starts_only_while_chat_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    # 켜져 있으면 재시작 복구가 실행을 이어 가므로 끝내지 않는다
    from pitch_coach_backend import main
    from pitch_coach_backend.core.config import settings

    started: list[bool] = []

    async def stop_nothing() -> None:
        return None

    def start() -> Any:
        started.append(True)
        return stop_nothing

    monkeypatch.setattr(main, "start_switch_off_sweep", start)
    monkeypatch.setattr(settings, "agent_enabled", enabled)

    async def run_app() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_app())

    assert started == ([] if enabled else [True])


def test_message_is_not_left_for_cancelled_queued_run(
    db_session,
    session_factory,  # noqa: F811
    user_id: uuid.UUID,
):
    # 승인 대기가 아닌 실행은 고정 문장을 남기지 않는다(화면 취소와 같다)
    run = _run(db_session, user_id, status="QUEUED")

    _close(session_factory)

    db_session.expire_all()
    assert (
        db_session.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id == run.conversation_id)
        ).all()
        == []
    )
