import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import cleanup_sweep
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatMessage,
    ChatProfileItem,
    ChatRun,
    ChatRunEvent,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.service import cleanup as cleanup_service
from pitch_coach_backend.module.chat.service import run as run_service
from pitch_coach_backend.module.chat.service import turn as turn_service
from pitch_coach_backend.module.chat.service.cleanup import delete_expired
from tests.chat.api_helpers import bearer


def _conversation(db: Session, user_id: uuid.UUID, last_active: datetime) -> ChatConversation:
    conversation = ChatConversation(
        user_id=user_id, title="질문", last_user_activity_at=last_active
    )
    db.add(conversation)
    db.flush()
    return conversation


def _run(
    db: Session,
    conversation: ChatConversation,
    status: str,
    finished_at: datetime | None,
) -> ChatRun:
    now = datetime.now(UTC)
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        first_trigger="USER_MESSAGE",
        status=status,
        input={"user_message": "어땠어?", "page": {"route": "home"}, "choice": None},
        accepted_at=now - timedelta(seconds=10),
        deadline_at=now + timedelta(seconds=110),
        finished_at=finished_at,
    )
    db.add(run)
    db.flush()
    return run


def _action(db: Session, run: ChatRun) -> ChatAction:
    action = ChatAction(
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        run_id=run.id,
        action_id="a-1",
        type="SAVE_GOAL",
        args={},
        args_digest="digest",
        autonomy="ASK",
        idempotency_key=f"key-{run.id}",
        status="SUCCEEDED",
        result={"status": "SUCCEEDED"},
    )
    db.add(action)
    db.flush()
    return action


def _run_log(db: Session, user_id: uuid.UUID, day: date) -> ChatRunLog:
    log = ChatRunLog(run_id=uuid.uuid4(), day=day, user_id=user_id, status="DONE", ms=1000)
    db.add(log)
    db.flush()
    return log


def _profile_item(
    db: Session,
    user_id: uuid.UUID,
    key: str,
    created_at: datetime,
    last_used_at: datetime | None,
) -> ChatProfileItem:
    item = ChatProfileItem(
        user_id=user_id,
        key=key,
        value="짧게 말해 줘",
        created_at=created_at,
        last_used_at=last_used_at,
    )
    db.add(item)
    db.flush()
    return item


def test_conversation_inactive_for_180_days_is_deleted_with_its_rows(
    db_session: Session, user_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    old = _conversation(db_session, user_id, now - timedelta(days=181))
    recent = _conversation(db_session, user_id, now - timedelta(days=179))
    db_session.add(
        ChatMessage(conversation_id=old.id, user_id=user_id, seq=1, role="user", text="질문")
    )
    old_run = _run(db_session, old, "DONE", now - timedelta(days=1))
    db_session.commit()
    old_id, recent_id, old_run_id = old.id, recent.id, old_run.id

    delete_expired(db_session, now)

    db_session.expire_all()
    assert db_session.get(ChatConversation, old_id) is None
    assert db_session.get(ChatRun, old_run_id) is None
    assert (
        db_session.scalars(select(ChatMessage).where(ChatMessage.conversation_id == old_id)).all()
        == []
    )
    assert db_session.get(ChatConversation, recent_id) is not None


def test_finished_run_is_deleted_after_7_days_but_its_action_stays(
    db_session: Session, user_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    conversation = _conversation(db_session, user_id, now)
    old = _run(db_session, conversation, "DONE", now - timedelta(days=8))
    recent = _run(db_session, conversation, "DONE", now - timedelta(days=6))
    running = _run(db_session, conversation, "RUNNING", None)
    db_session.add(ChatRunEvent(run_id=old.id, event_seq=1, event="plan", data={"plan": []}))
    action = _action(db_session, old)
    db_session.commit()
    old_id, recent_id, running_id, action_id = old.id, recent.id, running.id, action.id

    delete_expired(db_session, now)

    db_session.expire_all()
    assert db_session.get(ChatRun, old_id) is None
    assert db_session.scalars(select(ChatRunEvent).where(ChatRunEvent.run_id == old_id)).all() == []
    assert db_session.get(ChatAction, action_id) is not None
    assert db_session.get(ChatAction, action_id).result is None
    assert db_session.get(ChatRun, recent_id) is not None
    assert db_session.get(ChatRun, running_id) is not None


def test_run_logs_older_than_180_days_are_deleted(db_session: Session, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    today = now.astimezone(run_service.SEOUL).date()
    old = _run_log(db_session, user_id, today - timedelta(days=181))
    recent = _run_log(db_session, user_id, today - timedelta(days=179))
    db_session.commit()
    old_id, recent_id = old.id, recent.id

    delete_expired(db_session, now)

    db_session.expire_all()
    assert db_session.get(ChatRunLog, old_id) is None
    assert db_session.get(ChatRunLog, recent_id) is not None


def test_profile_items_unused_for_180_days_are_deleted(
    db_session: Session, user_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    long_ago = now - timedelta(days=300)
    unused = _profile_item(db_session, user_id, "TONE", long_ago, now - timedelta(days=181))
    used = _profile_item(db_session, user_id, "EXPLAIN_STYLE", long_ago, now - timedelta(days=10))
    never_used = _profile_item(db_session, user_id, "FOCUS_AREA", long_ago, None)
    new = _profile_item(db_session, user_id, "CONTEXT", now - timedelta(days=10), None)
    db_session.commit()
    unused_id, used_id, never_used_id, new_id = unused.id, used.id, never_used.id, new.id

    delete_expired(db_session, now)

    db_session.expire_all()
    assert db_session.get(ChatProfileItem, unused_id) is None
    assert db_session.get(ChatProfileItem, never_used_id) is None
    assert db_session.get(ChatProfileItem, used_id) is not None
    assert db_session.get(ChatProfileItem, new_id) is not None


def test_conversations_are_deleted_across_batches(
    db_session: Session, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup_service, "DELETE_BATCH", 1)
    now = datetime.now(UTC)
    ids = [_conversation(db_session, user_id, now - timedelta(days=200)).id for _ in range(3)]
    db_session.commit()

    delete_expired(db_session, now)

    db_session.expire_all()
    assert all(db_session.get(ChatConversation, id_) is None for id_ in ids)


def test_mark_sending_records_when_profile_items_were_sent(
    db_session: Session, user_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    sent = _profile_item(db_session, user_id, "TONE", now - timedelta(days=1), None)
    not_sent = _profile_item(db_session, user_id, "CONTEXT", now - timedelta(days=1), None)
    conversation = _conversation(db_session, user_id, now)
    run = _run(db_session, conversation, "QUEUED", None)
    db_session.commit()

    claimed = run_service.claim(db_session, run.id)
    turn_service.mark_sending(
        db_session, claimed, 1, "r1", {"pitches": [], "takes": []}, 0, None, [sent.id]
    )

    db_session.expire_all()
    assert db_session.get(ChatProfileItem, sent.id).last_used_at is not None
    assert db_session.get(ChatProfileItem, not_sent.id).last_used_at is None


def test_sweep_deletes_expired_rows_and_logs_the_count(
    db_session: Session, user_id: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)
    today = now.astimezone(run_service.SEOUL).date()
    old = _conversation(db_session, user_id, now - timedelta(days=181))
    log = _run_log(db_session, user_id, today - timedelta(days=181))
    db_session.commit()
    old_id, log_id = old.id, log.id

    def factory() -> Session:
        return Session(
            bind=db_session.connection(),
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )

    caplog.set_level(logging.INFO, logger=cleanup_sweep.logger.name)
    assert asyncio.run(cleanup_sweep.sweep_once(factory)) is True

    db_session.expire_all()
    assert db_session.get(ChatConversation, old_id) is None
    assert db_session.get(ChatRunLog, log_id) is None
    assert "보관 기간이 지난 대화 자료 2개를 지웠다" in [r.getMessage() for r in caplog.records]


def test_recently_opened_conversation_is_kept(db_session: Session, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    opened = _conversation(db_session, user_id, now - timedelta(days=181))
    opened.last_opened_at = now - timedelta(days=10)
    stale = _conversation(db_session, user_id, now - timedelta(days=181))
    stale.last_opened_at = now - timedelta(days=181)
    db_session.commit()
    opened_id, stale_id = opened.id, stale.id

    delete_expired(db_session, now)

    db_session.expire_all()
    assert db_session.get(ChatConversation, opened_id) is not None
    assert db_session.get(ChatConversation, stale_id) is None


@pytest.mark.usefixtures("chat_app")
def test_opening_the_first_page_records_when_it_was_opened(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    conversation = _conversation(db_session, user_id, datetime.now(UTC))
    db_session.commit()
    path = f"/api/chat/conversations/{conversation.id}/messages"

    assert client.get(f"{path}?before_seq=5", headers=bearer(user_id)).status_code == 200
    db_session.expire_all()
    assert db_session.get(ChatConversation, conversation.id).last_opened_at is None

    assert client.get(path, headers=bearer(user_id)).status_code == 200
    db_session.expire_all()
    assert db_session.get(ChatConversation, conversation.id).last_opened_at is not None
