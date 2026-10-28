"""코치 연동 inputs 저장 규칙 회귀 테스트."""





import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.dto import SlideEvent
from pitch_coach_backend.module.take.entity import (
    Take,
    TakeSlideEvent,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.take.service import record as record_service
from pitch_coach_backend.module.user.entity import User


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", name="코치")
    db_session.add(user)
    db_session.flush()
    return user

@pytest.fixture
def pitch(db_session: Session, user: User) -> Pitch:
    pitch = Pitch(
        user_id=user.id, title="발표", time_limit_sec=300, upper_deviation=30, lower_deviation=30
    )
    db_session.add(pitch)
    db_session.flush()
    return pitch

@pytest.fixture
def script(db_session: Session, pitch: Pitch) -> ScriptVersion:
    script = ScriptVersion(
        pitch_id=pitch.id,
        version=1,
        content="대본",
        parse_status="DONE",
        # 파싱할 때의 목표는 제한 시간(300초)이었다
        parse_meta={"target_ms": 300_000},
    )
    db_session.add(script)
    db_session.flush()
    db_session.add_all(
        [
            ScriptSlide(
                script_version_id=script.id,
                slide_number=n,
                full_content=f"{n}번 장 대본",
                script_chars=100,
                expected_ms=100_000,
            )
            for n in (1, 2, 3)
        ]
    )
    db_session.flush()
    return script

@pytest.fixture
def presentation(db_session: Session, pitch: Pitch) -> PresentationVersion:
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="deck.pdf")
    db_session.add(presentation)
    db_session.flush()
    return presentation

def make_take(
    db: Session,
    pitch: Pitch,
    script: ScriptVersion,
    presentation: PresentationVersion,
    number: int,
    **fields: Any,
) -> Take:
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode=fields.pop("mode", "COACHING"),
        script_mode=fields.pop("script_mode", "HIGHLIGHT"),
        goal_time_sec=fields.pop("goal_time_sec", 300),
        status=fields.pop("status", "READY"),
        **fields,
    )
    db.add(take)
    db.flush()
    return take

@pytest.fixture
def take_parts(pitch: Pitch, script: ScriptVersion, presentation: PresentationVersion):
    return pitch, script, presentation

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
