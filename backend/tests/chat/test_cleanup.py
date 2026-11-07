"""대화 정리 작업이 지워진 근거의 답 · 실행 · 목표를 비우고 다시 고르지 않는다."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import (
    ChatConversation,
    ChatGoal,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.cleanup import clean_deleted_sources


@pytest.fixture
def conversation(db_session: Session, user_id: uuid.UUID) -> ChatConversation:
    row = ChatConversation(user_id=user_id, title="질문")
    db_session.add(row)
    db_session.commit()
    return row


def _answer(db_session: Session, conversation: ChatConversation, take_id: uuid.UUID) -> None:
    db_session.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            seq=1,
            role="assistant",
            text="말이 빨랐어요",
            status="DONE",
            assessments=[{"kind": "PACE"}],
            source_take_ids=[str(take_id)],
        )
    )
    db_session.commit()


def test_goal_with_deleted_base_take_is_emptied(
    db_session: Session, conversation: ChatConversation
) -> None:
    conversation.goal_version = 1
    db_session.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=1,
            goal={"text": "말 빠르기 줄이기"},
            status="OPEN",
            base_take_id=uuid.uuid4(),
            first_take_number=2,
        )
    )
    db_session.commit()

    assert clean_deleted_sources(db_session) == 1

    emptied = db_session.scalar(
        select(func.count())
        .select_from(ChatGoal)
        .where(
            ChatGoal.conversation_id == conversation.id,
            ChatGoal.version == 1,
            ChatGoal.goal.is_(None),
            ChatGoal.status.is_(None),
        )
    )
    assert emptied == 1
    assert clean_deleted_sources(db_session) == 0


def test_masked_answer_does_not_break_recent_assessments(
    db_session: Session, conversation: ChatConversation
) -> None:
    _answer(db_session, conversation, uuid.uuid4())

    assert clean_deleted_sources(db_session) == 1

    recent = ChatRepository(db_session).list_recent_assessments(
        conversation.id, before_seq=None, limit=5
    )
    assert recent == []


def test_masked_run_is_not_picked_again(
    db_session: Session, conversation: ChatConversation
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        ChatRun(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            first_trigger="USER_MESSAGE",
            status="DONE",
            input={"user_message": "어땠어?", "page": None, "choice": None},
            accepted_at=now - timedelta(seconds=10),
            deadline_at=now + timedelta(seconds=110),
            plan={"steps": []},
            read_sources={"takes": [str(uuid.uuid4())]},
        )
    )
    db_session.commit()

    assert clean_deleted_sources(db_session) == 1

    assert ChatRepository(db_session).list_finished_runs_with_content() == []
