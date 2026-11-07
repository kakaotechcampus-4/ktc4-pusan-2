import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import ChatConversation, ChatGoal, ChatLease
from pitch_coach_backend.module.chat.service.goal_check import pick_targets
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take, TakeAIResults


def _pitch(db: Session, user_id: uuid.UUID) -> tuple[Pitch, PresentationVersion, ScriptVersion]:
    pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db.add_all([presentation, script])
    db.flush()
    return pitch, presentation, script


def _take(
    db: Session,
    parts: tuple[Pitch, PresentationVersion, ScriptVersion],
    number: int,
    *,
    status: str = "COMPLETED",
    review_revision: int | None = None,
) -> Take:
    pitch, presentation, script = parts
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status=status,
        review_status="DONE" if review_revision is not None else None,
    )
    db.add(take)
    db.flush()
    if review_revision is not None:
        db.add(TakeAIResults(take_id=take.id, kind="REVIEW", payload={}, revision=review_revision))
        db.flush()
    return take


def _goal(db: Session, user_id: uuid.UUID, pitch: Pitch, first_take_number: int) -> uuid.UUID:
    conversation = ChatConversation(user_id=user_id, title="목표", goal_version=1)
    db.add(conversation)
    db.flush()
    db.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=1,
            goal={"pitch_id": str(pitch.id)},
            status="OPEN",
            first_take_number=first_take_number,
        )
    )
    db.flush()
    return conversation.id


def _lease(
    db: Session, conversation_id: uuid.UUID, take: Take, *, status: str, until: datetime
) -> None:
    db.add(
        ChatLease(
            kind="GOAL_CHECK",
            conversation_id=conversation_id,
            goal_version=1,
            take_id=take.id,
            review_revision=1,
            status=status,
            lease_until=until,
        )
    )
    db.flush()


def test_pick_targets_reads_each_goal_from_its_own_first_take(
    db_session: Session, user_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    parts = _pitch(db_session, user_id)
    first = _take(db_session, parts, 1, review_revision=1)
    _take(db_session, parts, 2, status="FAILED")
    checked = _take(db_session, parts, 3, review_revision=1)
    latest = _take(db_session, parts, 4, review_revision=1)
    pending_parts = _pitch(db_session, user_id)
    _take(db_session, pending_parts, 1)
    # 같은 피치라도 목표마다 시작 번호 · 확인 기록이 다르다
    from_second = _goal(db_session, user_id, parts[0], 2)
    _lease(db_session, from_second, checked, status="DONE", until=now)
    from_first = _goal(db_session, user_id, parts[0], 1)
    _lease(db_session, from_first, checked, status="PENDING", until=now + timedelta(minutes=5))
    busy = _goal(db_session, user_id, parts[0], 4)
    _lease(db_session, busy, latest, status="PENDING", until=now + timedelta(minutes=5))
    waiting_review = _goal(db_session, user_id, pending_parts[0], 1)

    statements: list[str] = []
    connection = db_session.connection()

    def count(conn, cursor, statement, *rest) -> None:
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", count)
    try:
        picked = pick_targets(db_session, now)
    finally:
        event.remove(connection, "before_cursor_execute", count)

    # 목표 수와 관계없이 열린 목표 · Take · 확인 기록을 한 번씩 읽는다
    assert len(statements) == 3
    targets = {target.conversation_id: target for target in picked}

    assert set(targets) == {from_second, from_first}
    assert (targets[from_second].take_id, targets[from_second].pitch_id) == (latest.id, parts[0].id)
    assert targets[from_first].take_id == first.id
    assert busy not in targets and waiting_review not in targets
