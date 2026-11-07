"""대화 정리 작업이 지워진 근거의 답 · 실행 · 목표 · 요약을 비우고 다시 고르지 않는다."""

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
    ChatSummary,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.cleanup import clean_deleted_sources
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT
from pitch_coach_backend.module.pitch.entity import Pitch


@pytest.fixture
def conversation(db_session: Session, user_id: uuid.UUID) -> ChatConversation:
    row = ChatConversation(user_id=user_id, title="질문")
    db_session.add(row)
    db_session.commit()
    return row


def _answer(db_session: Session, conversation: ChatConversation, take_id: object) -> None:
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


def test_answer_is_masked_when_its_deleted_source_id_is_not_in_canonical_form(
    db_session: Session, conversation: ChatConversation
) -> None:
    """저장된 근거 ID 가 대문자여도 지워졌으면 가린다."""
    _answer(db_session, conversation, str(uuid.uuid4()).upper())

    assert clean_deleted_sources(db_session) == 1

    message = db_session.scalar(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
    )
    assert message.text == MASKED_TEXT


def test_answer_stays_when_its_existing_source_id_is_not_in_canonical_form(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    db_session.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            seq=1,
            role="assistant",
            text="말이 빨랐어요",
            status="DONE",
            source_pitch_ids=[str(pitch.id).upper()],
        )
    )
    db_session.commit()

    assert clean_deleted_sources(db_session) == 0


def test_summary_is_emptied_when_its_deleted_source_id_is_not_in_canonical_form(
    db_session: Session, conversation: ChatConversation
) -> None:
    summary = _summary(db_session, conversation, pitch_ids=[], take_ids=[str(uuid.uuid4()).upper()])

    assert clean_deleted_sources(db_session) == 1

    db_session.expire_all()
    assert summary.items == []


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


def _summary(
    db_session: Session,
    conversation: ChatConversation,
    *,
    pitch_ids: list[str],
    take_ids: list[str],
) -> ChatSummary:
    message = ChatMessage(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        seq=1,
        role="user",
        text="3장을 어떻게 고칠까?",
        status="DONE",
    )
    db_session.add(message)
    db_session.flush()
    summary = ChatSummary(
        conversation_id=conversation.id,
        items=[{"kind": "TOPIC", "text": "3장 시선 고치기"}],
        summary_version=2,
        covered_message_id=message.id,
        source_pitch_ids=pitch_ids,
        source_take_ids=take_ids,
    )
    db_session.add(summary)
    db_session.commit()
    return summary


def test_summary_from_deleted_take_is_emptied_once(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    other = ChatConversation(user_id=user_id, title="다른 질문")
    db_session.add_all([pitch, other])
    db_session.commit()
    masked = _summary(db_session, conversation, pitch_ids=[], take_ids=[str(uuid.uuid4())])
    kept = _summary(db_session, other, pitch_ids=[str(pitch.id)], take_ids=[])
    covered = masked.covered_message_id

    assert clean_deleted_sources(db_session) == 1

    db_session.expire_all()
    assert masked.items == []
    assert masked.source_pitch_ids == []
    assert masked.source_take_ids == []
    assert masked.summary_version == 3
    assert masked.covered_message_id == covered
    assert kept.items == [{"kind": "TOPIC", "text": "3장 시선 고치기"}]
    assert kept.source_pitch_ids == [str(pitch.id)]
    assert kept.summary_version == 2
    assert clean_deleted_sources(db_session) == 0
