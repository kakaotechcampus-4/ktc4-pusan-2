"""코치 연동 inputs 저장 규칙 회귀 테스트."""





import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.dto import SlideEvent
from pitch_coach_backend.module.take.entity import (
    TakeSlideEvent,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.take.service import record as record_service
from tests.coach_storage_seed import (  # noqa: F401
    make_take,
    pitch,
    presentation,
    script,
    take_parts,
    user,
)


def test_slide_events_keep_the_first_of_each_time(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="RUNNING")

    coach_service.append_slide_events(
        db_session, take.id, [SlideEvent(1, 0), SlideEvent(2, 59_390)]
    )
    coach_service.append_slide_events(
        db_session, take.id, [SlideEvent(9, 59_390), SlideEvent(3, 90_000)]
    )

    rows = db_session.scalars(
        select(TakeSlideEvent)
        .where(TakeSlideEvent.take_id == take.id)
        .order_by(TakeSlideEvent.started_ms)
    ).all()
    assert [(r.slide_number, r.started_ms) for r in rows] == [(1, 0), (2, 59_390), (3, 90_000)]

    with pytest.raises(NonExistentTake):
        coach_service.append_slide_events(db_session, uuid.uuid7(), [SlideEvent(1, 0)])

def test_ending_a_take_starts_finalizing_with_the_utterance_ends(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="RUNNING", utterance_ends=[100])

    assert (
        lifecycle_service.mark_ended(db_session, take.id, utterance_ends=[89_400, 120_000])
        is True
    )

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "FINALIZING")
    assert take.analysis_stage_at is not None
    assert take.utterance_ends == [89_400, 120_000]

def test_ending_without_utterance_ends_keeps_what_was_saved(db_session: Session, take_parts):
    """이 프로세스가 Take 를 모르면(다시 뜸 · 되살린 Take) 메모리에 문장 끝 신호가 없다."""
    take = make_take(db_session, *take_parts, 1, status="RUNNING", utterance_ends=[100])

    lifecycle_service.mark_ended(db_session, take.id)

    db_session.refresh(take)
    assert take.utterance_ends == [100]

def test_utterance_ends_are_written_down_only_while_the_take_runs(db_session: Session, take_parts):
    """앱을 내릴 때 적어 둔다. 끝난 Take 는 끝낼 때 적은 값을 둔다."""
    running = make_take(db_session, *take_parts, 1, status="RUNNING")
    ended = make_take(db_session, *take_parts, 2, status="ANALYZING", utterance_ends=[100])

    assert record_service.save_utterance_ends(db_session, running.id, [1_500, 9_000]) is True
    assert record_service.save_utterance_ends(db_session, ended.id, [7]) is False

    assert record_service.stored_utterance_ends(db_session, running.id) == [1_500, 9_000]
    assert record_service.stored_utterance_ends(db_session, ended.id) == [100]
    assert record_service.stored_utterance_ends(db_session, uuid.uuid7()) == []

def test_take_left_behind_keeps_its_utterance_ends(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="RUNNING")

    lifecycle_service.mark_abandoned(
        db_session, take.id, datetime.now(UTC), "ABANDONED", utterance_ends=[1_500]
    )

    db_session.refresh(take)
    assert (take.status, take.end_reason, take.utterance_ends) == ("FAILED", "ABANDONED", [1_500])
