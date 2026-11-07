import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatMessage,
    ChatRun,
    ChatRunEvent,
)
from pitch_coach_backend.module.chat.service.run import OWNER, SEOUL
from pitch_coach_backend.module.chat.service.turn import recover_run

STATE = {"v": 1, "kid": "k1", "payload": "p", "signature": "s"}


class _TestSession(Session):
    def connection(self, bind_arguments=None, execution_options=None):
        return super().connection(bind_arguments)


@pytest.fixture
def session_factory(db_session: Session):
    def make() -> Session:
        return _TestSession(
            bind=db_session.connection(),
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )

    return make


@pytest.fixture
def conversation(db_session: Session, user_id: uuid.UUID) -> ChatConversation:
    row = ChatConversation(user_id=user_id, title="질문")
    db_session.add(row)
    db_session.flush()
    return row


def _run(
    db_session: Session, conversation: ChatConversation, now: datetime, **fields: Any
) -> ChatRun:
    message = ChatMessage(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        seq=1,
        role="user",
        text="어땠어?",
    )
    db_session.add(message)
    db_session.flush()
    values = {
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "message_id": message.id,
        "first_trigger": "USER_MESSAGE",
        "status": "RUNNING",
        "input": {"user_message": "어땠어?", "page": None, "choice": None},
        "accepted_at": now - timedelta(seconds=10),
        "deadline_at": now + timedelta(seconds=110),
        **fields,
    }
    row = ChatRun(**values)
    db_session.add(row)
    db_session.flush()
    return row


def _action(db_session: Session, run: ChatRun, **fields: Any) -> ChatAction:
    values = {
        "conversation_id": run.conversation_id,
        "user_id": run.user_id,
        "run_id": run.id,
        "action_id": "a-1",
        "type": "SAVE_GOAL",
        "args": {"expected_goal_version": 0, "goal": {}},
        "args_digest": "d",
        "autonomy": "ASK",
        "idempotency_key": f"{run.id}:1",
        "args_hash": "sha256:" + "0" * 64,
        "status": "PENDING",
        **fields,
    }
    row = ChatAction(**values)
    db_session.add(row)
    db_session.flush()
    return row


def _call(stage: str) -> list[dict[str, Any]]:
    return [{"turn_call": 1, "request_id": "r1", "state_revision": None, "stage": stage}]


def _recover(session_factory, run_id: uuid.UUID, now: datetime) -> str | None:
    with session_factory() as db:
        return recover_run(db, run_id, now)


def _reload(db_session: Session, row: Any) -> Any:
    db_session.expire_all()
    return db_session.get(type(row), row.id)


def test_queued_run_without_calls_starts_again(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, status="QUEUED")

    assert _recover(session_factory, run.id, now) == "START"

    run = _reload(db_session, run)
    assert (run.status, run.lease_owner) == ("QUEUED", OWNER)


def test_live_lease_is_left_alone(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(
        db_session, conversation, now, lease_owner="other", lease_until=now + timedelta(seconds=20)
    )
    revision = run.row_revision

    assert _recover(session_factory, run.id, now) is None

    run = _reload(db_session, run)
    assert (run.status, run.row_revision, run.lease_owner) == ("RUNNING", revision, "other")


def test_run_past_deadline_times_out(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, deadline_at=now - timedelta(seconds=1))

    assert _recover(session_factory, run.id, now) is None

    run = _reload(db_session, run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_TIMEOUT")


def test_unsent_run_returns_its_reservation(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, deadline_at=now - timedelta(seconds=1))
    day = run.accepted_at.astimezone(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=None, day=day, reserved=1))
    db_session.flush()

    _recover(session_factory, run.id, now)

    db_session.expire_all()
    usage = db_session.scalar(
        select(ChatDailyUsage).where(ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day)
    )
    assert (usage.reserved, usage.sent) == (0, 0)


def test_sending_call_is_not_sent_again(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, turn_calls=_call("SENDING"))

    assert _recover(session_factory, run.id, now) is None

    run = _reload(db_session, run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")
    assert (run.usage["measured"], run.usage["missing_reason"]) == (False, "RESPONSE_LOST")


def test_sending_call_keeps_received_claims_as_partial(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, turn_calls=_call("SENDING"))
    claim = {"kind": "SYSTEM", "text": "여기까지 봤어요", "evidence_ids": [], "source_ids": []}
    db_session.add(
        ChatRunEvent(
            run_id=run.id,
            event_seq=1,
            event="claim",
            data={"index": 0, "claim": claim, "evidence": [], "sources": [], "assessments": []},
        )
    )
    db_session.flush()

    assert _recover(session_factory, run.id, now) is None

    run = _reload(db_session, run)
    assert run.status == "PARTIAL"


def test_action_waiting_for_a_card_goes_back_to_approval(
    db_session, session_factory, conversation
):
    now = datetime.now(UTC)
    card = {"type": "ASK_CONFIRM", "action_id": "a-1"}
    run = _run(
        db_session,
        conversation,
        now,
        turn_calls=_call("DONE"),
        state=STATE,
        interim_message={"message": {"cards": [card]}},
    )
    _action(db_session, run)

    assert _recover(session_factory, run.id, now) is None

    run = _reload(db_session, run)
    assert run.status == "WAITING_APPROVAL"


def test_preapproved_action_without_a_card_is_failed_and_resumed(
    db_session, session_factory, conversation
):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, turn_calls=_call("DONE"), state=STATE)
    action = _action(db_session, run)

    assert _recover(session_factory, run.id, now) == "RESUME"

    action = _reload(db_session, action)
    assert action.status == "FAILED"
    assert action.result["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert action.result["executed_args_hash"].startswith("sha256:")


def test_running_action_is_settled_before_resume(db_session, session_factory, conversation):
    now = datetime.now(UTC)
    run = _run(db_session, conversation, now, turn_calls=_call("DONE"), state=STATE)
    action = _action(db_session, run, status="RUNNING", approved=True)

    assert _recover(session_factory, run.id, now) == "RESUME"

    action = _reload(db_session, action)
    assert (action.status, action.result["status"]) == ("FAILED", "FAILED")
