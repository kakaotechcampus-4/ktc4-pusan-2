"""대화 정리 작업이 지워진 근거의 답 · 실행 · 목표 · 요약을 비우고 다시 고르지 않는다."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.dto import HISTORY_MESSAGES
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDeletedSource,
    ChatGoal,
    ChatMessage,
    ChatRun,
    ChatRunEvent,
    ChatSummary,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service import cleanup as cleanup_service
from pitch_coach_backend.module.chat.service.memory import save_summary
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT
from pitch_coach_backend.module.chat.service.turn import save_claim_message
from pitch_coach_backend.module.chat.service.waiting import read_emitted
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take


@pytest.fixture
def conversation(db_session: Session, user_id: uuid.UUID) -> ChatConversation:
    row = ChatConversation(user_id=user_id, title="질문")
    db_session.add(row)
    db_session.commit()
    return row


def _clean(db_session: Session) -> int:
    return cleanup_service.clean_deleted_sources(db_session, datetime.now(UTC))


def _pitch(db_session: Session, user_id: uuid.UUID) -> Pitch:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    db_session.add_all(
        [
            PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
            ScriptVersion(pitch_id=pitch.id, version=1, content="대본"),
        ]
    )
    db_session.commit()
    return pitch


def _take(db_session: Session, pitch: Pitch) -> Take:
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=db_session.scalars(
            select(PresentationVersion.id).filter_by(pitch_id=pitch.id)
        ).one(),
        script_version_id=db_session.scalars(
            select(ScriptVersion.id).filter_by(pitch_id=pitch.id)
        ).one(),
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db_session.add(take)
    db_session.commit()
    return take


def _deleted_take_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    take = _take(db_session, _pitch(db_session, user_id))
    take_id = take.id
    db_session.delete(take)
    db_session.commit()
    return take_id


def _answer(
    db_session: Session, conversation: ChatConversation, take_id: uuid.UUID, seq: int = 1
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        seq=seq,
        role="assistant",
        text="말이 빨랐어요",
        status="DONE",
        assessments=[{"kind": "PACE"}],
        source_take_ids=[str(take_id)],
    )
    db_session.add(message)
    db_session.commit()
    return message


def _finished_run(
    db_session: Session, conversation: ChatConversation, read_sources: dict | None
) -> ChatRun:
    now = datetime.now(UTC)
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        first_trigger="USER_MESSAGE",
        status="DONE",
        input={"user_message": "어땠어?", "page": None, "choice": None},
        accepted_at=now - timedelta(seconds=10),
        deadline_at=now + timedelta(seconds=110),
        plan={"steps": []},
        read_sources=read_sources,
    )
    db_session.add(run)
    db_session.commit()
    return run


def _recorded(db_session: Session) -> set[uuid.UUID]:
    return set(db_session.scalars(select(ChatDeletedSource.source_id)))


def test_deleting_pitch_records_its_cascaded_takes(db_session: Session, user_id: uuid.UUID) -> None:
    pitch = _pitch(db_session, user_id)
    take = _take(db_session, pitch)
    pitch_id, take_id = pitch.id, take.id
    db_session.expunge(take)

    db_session.delete(pitch)
    db_session.commit()

    assert {pitch_id, take_id} <= _recorded(db_session)


def test_goal_with_deleted_base_take_is_emptied(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    conversation.goal_version = 1
    db_session.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=1,
            goal={"text": "말 빠르기 줄이기"},
            status="OPEN",
            base_take_id=_deleted_take_id(db_session, user_id),
            first_take_number=2,
        )
    )
    db_session.commit()

    assert _clean(db_session) == 1

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
    assert _clean(db_session) == 0


def test_masked_answer_does_not_break_recent_assessments(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    _answer(db_session, conversation, _deleted_take_id(db_session, user_id))

    assert _clean(db_session) == 1

    recent = ChatRepository(db_session).list_recent_assessments(
        conversation.id, before_seq=None, limit=5
    )
    assert recent == []


def test_answer_with_existing_source_stays(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    deleted = _deleted_take_id(db_session, user_id)
    masked = _answer(db_session, conversation, deleted)
    kept_take = _take(db_session, _pitch(db_session, user_id))
    kept = _answer(db_session, conversation, kept_take.id, seq=2)

    assert _clean(db_session) == 1

    db_session.expire_all()
    assert masked.text == MASKED_TEXT
    assert kept.text == "말이 빨랐어요"


@pytest.mark.parametrize("key", ["pitches", "takes"])
def test_masked_run_is_not_picked_again(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID, key: str
) -> None:
    deleted = _deleted_take_id(db_session, user_id)
    if key == "pitches":
        pitch = _pitch(db_session, user_id)
        deleted = pitch.id
        db_session.delete(pitch)
        db_session.commit()
    run = _finished_run(db_session, conversation, {key: [str(deleted)]})

    assert _clean(db_session) == 1

    db_session.expire_all()
    assert run.plan is None
    assert _clean(db_session) == 0


def test_run_from_action_source_clears_events_and_results(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    deleted = _deleted_take_id(db_session, user_id)
    run = _finished_run(db_session, conversation, None)
    kept = _finished_run(db_session, conversation, None)
    action = ChatAction(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        run_id=run.id,
        action_id="a1",
        type="FETCH_TAKE",
        args={},
        args_digest="d",
        autonomy="AUTO",
        idempotency_key="k1",
        status="SUCCEEDED",
        result={"pace": 1},
        source_take_ids=[str(deleted)],
    )
    event = ChatRunEvent(run_id=run.id, event_seq=1, event="plan", data={"steps": []})
    db_session.add_all([action, event])
    db_session.commit()

    assert _clean(db_session) == 1

    db_session.expire_all()
    assert run.plan is None
    assert action.result is None
    assert db_session.scalar(select(func.count()).where(ChatRunEvent.run_id == run.id)) == 0
    assert kept.plan == {"steps": []}


def test_run_finished_after_cleanup_is_masked_next_time(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    deleted = _deleted_take_id(db_session, user_id)
    assert _clean(db_session) == 0

    run = _finished_run(db_session, conversation, {"takes": [str(deleted)]})

    assert _clean(db_session) == 1
    db_session.expire_all()
    assert run.plan is None


def test_old_records_go_after_being_read_and_recent_stay(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    old = _deleted_take_id(db_session, user_id)
    recent = _deleted_take_id(db_session, user_id)
    message = _answer(db_session, conversation, old)
    now = datetime.now(UTC)
    db_session.execute(
        update(ChatDeletedSource)
        .where(ChatDeletedSource.source_id == old)
        .values(deleted_at=now - cleanup_service.DELETED_SOURCE_KEEP - timedelta(seconds=1))
    )
    db_session.commit()

    assert cleanup_service.clean_deleted_sources(db_session, now) == 1

    db_session.expire_all()
    assert message.text == MASKED_TEXT
    recorded = _recorded(db_session)
    assert old not in recorded
    assert recent in recorded


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
    pitch = _pitch(db_session, user_id)
    other = ChatConversation(user_id=user_id, title="다른 질문")
    db_session.add(other)
    db_session.commit()
    deleted = _deleted_take_id(db_session, user_id)
    masked = _summary(db_session, conversation, pitch_ids=[], take_ids=[str(deleted)])
    kept = _summary(db_session, other, pitch_ids=[str(pitch.id)], take_ids=[])
    covered = masked.covered_message_id

    assert _clean(db_session) == 1

    db_session.expire_all()
    assert masked.items == []
    assert masked.source_pitch_ids == []
    assert masked.source_take_ids == []
    assert masked.summary_version == 3
    assert masked.covered_message_id == covered
    assert kept.items == [{"kind": "TOPIC", "text": "3장 시선 고치기"}]
    assert kept.source_pitch_ids == [str(pitch.id)]
    assert kept.summary_version == 2
    assert _clean(db_session) == 0


def test_summary_covering_masked_answer_is_saved_empty(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    deleted = _deleted_take_id(db_session, user_id)
    _answer(db_session, conversation, deleted)
    assert _clean(db_session) == 1
    db_session.add_all(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=user_id,
            seq=seq,
            role="user",
            text="질문",
            status="DONE",
        )
        for seq in range(2, HISTORY_MESSAGES + 2)
    )
    run = _finished_run(db_session, conversation, None)
    run.turn_calls = [{"turn_call": 1, "summary_version": 0}]
    db_session.commit()

    save_summary(
        ChatRepository(db_session),
        run,
        1,
        {"history_summary": [{"kind": "TOPIC", "text": "요약"}]},
    )
    db_session.commit()

    summary = ChatRepository(db_session).get_summary(conversation.id)
    assert summary.items == []
    assert summary.source_take_ids == []
    assert summary.summary_version == 1


def _claim_with(db_session: Session, run: ChatRun, take_id: uuid.UUID, pitch_id: uuid.UUID) -> None:
    db_session.add(
        ChatRunEvent(
            run_id=run.id,
            event_seq=1,
            event="claim",
            data={
                "claim": {"kind": "OBSERVATION", "text": "말이 빨랐어요"},
                "evidence": [
                    {"pitch_id": str(pitch_id).upper(), "take_id": str(take_id).replace("-", "")}
                ],
            },
        )
    )
    db_session.commit()


def test_claim_answer_with_odd_id_format_is_masked(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    pitch = _pitch(db_session, user_id)
    take = _take(db_session, pitch)
    run = _finished_run(db_session, conversation, None)
    _claim_with(db_session, run, take.id, pitch.id)

    message = save_claim_message(db_session, run, "PARTIAL")
    db_session.commit()
    assert message.source_pitch_ids == [str(pitch.id)]
    assert message.source_take_ids == [str(take.id)]

    db_session.delete(take)
    db_session.commit()

    assert _clean(db_session) >= 1
    db_session.expire_all()
    assert message.text == MASKED_TEXT


def test_read_emitted_keeps_canonical_ids(
    db_session: Session, conversation: ChatConversation, user_id: uuid.UUID
) -> None:
    pitch = _pitch(db_session, user_id)
    take = _take(db_session, pitch)
    run = _finished_run(db_session, conversation, None)
    _claim_with(db_session, run, take.id, pitch.id)

    emitted = read_emitted(db_session, run.id)

    assert emitted.pitch_ids == [str(pitch.id)]
    assert emitted.take_ids == [str(take.id)]
