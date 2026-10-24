"""코치 연동 context 저장 규칙 회귀 테스트."""





import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.requests import SlideEvent
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
    StandardAnalyses,
)
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.entity import (
    Calibration,
    CoachEvent,
    Mission,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.user.entity import User

META = {"schema_version": "1.0", "feature_version": "coach-1.2", "model": None}

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

def reviewed(db: Session, take: Take, *, completed_at: datetime | None, **review: Any) -> Take:
    """리뷰까지 끝난 Take 로 만든다."""
    take.status = "COMPLETED"
    take.review_status = "DONE"
    take.completed_at = completed_at
    db.add(TakeAIResults(take_id=take.id, kind="REVIEW", payload=review, meta=META))
    db.flush()
    return take

def mission(db: Session, source: Take, priority: int, **fields: Any) -> Mission:
    row = Mission(
        source_take_id=source.id,
        area=fields.pop("area", "GAZE"),
        slide_number=fields.pop("slide_number", None),
        target=fields.pop("target", {"metric": "script_ratio", "operator": "LTE", "value": 0.3}),
        description=fields.pop("description", f"미션 {priority}"),
        priority=priority,
        status=fields.pop("status", "ACTIVE"),
        **fields,
    )
    db.add(row)
    db.flush()
    return row

@pytest.fixture
def take_parts(pitch: Pitch, script: ScriptVersion, presentation: PresentationVersion):
    return pitch, script, presentation

def test_reloaded_coach_context_has_the_first_saved_metrics(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1)
    coach_service.start_take(db_session, take.id)
    db_session.add(TakeMetricSecond(take_id=take.id, t_ms=1000, stt_status="degraded"))
    db_session.flush()
    context = coach_store.load_take_context(db_session, take.id)
    assert context is not None
    assert len(context.seconds) == 1
    assert (context.seconds[0].t_ms, context.seconds[0].gaze) == (1000, None)
    assert context.seconds[0].stt_status == "degraded"

def test_take_context_has_the_take_constants(db_session: Session, take_parts):
    pitch, script, presentation = take_parts
    source = reviewed(
        db_session,
        make_take(db_session, *take_parts, 1),
        completed_at=datetime.now(UTC),
        recurring_issues=[{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
    )
    row = mission(db_session, source, 1, description="대본 보지 않기", slide_number=2)
    db_session.add(
        StandardAnalyses(
            pitch_id=pitch.id, version=1, targets=[], excluded=[], meta=META, script_used=True
        )
    )
    # 지금 Take 의 목표 시간은 제한 시간과 다르다. 장 목표는 이 목표에 맞춰 비율로 바뀐다
    take = make_take(db_session, *take_parts, 2, goal_time_sec=240, mode="EXAM")
    coach_service.start_take(db_session, take.id)
    db_session.add(
        Calibration(take_id=take.id, base_volume=0, gaze_confidence=0.9, base_level_db=-29.5)
    )
    db_session.add(TakeSlideEvent(take_id=take.id, slide_number=2, started_ms=59_390))
    db_session.flush()

    context = coach_store.load_take_context(db_session, take.id)

    assert context is not None
    constants = context.constants
    common = constants.common()
    assert common["take_id"] == str(take.id)
    assert common["mode"] == "EXAM"
    assert common["plan"]["target_ms"] == 240_000
    assert (common["plan"]["min_ms"], common["plan"]["max_ms"]) == (270_000, 330_000)
    assert [s["target_ms"] for s in common["plan"]["slides"]] == [80_000, 80_000, 80_000]
    assert common["missions"] == [
        {
            "mission_id": str(row.id),
            "area": "GAZE",
            "slide_number": 2,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.3},
            "priority": 1,
            "description": "대본 보지 않기",
        }
    ]
    assert common["recurring_issues"] == [
        {"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}
    ]
    assert common["script_used"] is True
    assert common["calibration"] == {"base_level_db": -29.5}
    assert common["coaching_plan"] is None
    assert constants.script_mode == "HIGHLIGHT"
    assert context.slides == (SlideEvent(2, 59_390),)

def test_missions_deleted_after_the_take_started_are_skipped(db_session: Session, take_parts):
    source = reviewed(db_session, make_take(db_session, *take_parts, 1), completed_at=None)
    kept = mission(db_session, source, 1)
    deleted = mission(db_session, source, 2)
    take = make_take(db_session, *take_parts, 2)
    coach_service.start_take(db_session, take.id)
    db_session.delete(deleted)
    db_session.flush()

    context = coach_store.load_take_context(db_session, take.id)

    assert context is not None
    assert [m["mission_id"] for m in context.constants.missions] == [str(kept.id)]

def _event(event_id: str, t_ms: int, kind: str = "INTERVENTION") -> dict[str, Any]:
    return {"kind": kind, "event_id": event_id, "t_ms": t_ms, "evidence": {"start_ms": 0}}

def test_events_keep_the_received_order_and_skip_the_same_id(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="RUNNING")

    coach_service.append_coach_events(
        db_session,
        take.id,
        [(_event("iv-1000", 1000), META), (_event("ep-a", 900, "EPISODE"), META)],
    )
    coach_service.append_coach_events(
        db_session,
        take.id,
        [(_event("iv-1000", 1000), META), (_event("su-b", 2000, "SUPPRESSED"), META)],
    )

    rows = db_session.scalars(
        select(CoachEvent).where(CoachEvent.take_id == take.id).order_by(CoachEvent.seq)
    ).all()
    assert [(r.seq, r.event_id, r.kind, r.t_ms) for r in rows] == [
        (1, "iv-1000", "INTERVENTION", 1000),
        (2, "ep-a", "EPISODE", 900),
        (4, "su-b", "SUPPRESSED", 2000),
    ]
    # 이벤트 JSON 그대로, 그 응답의 meta 와 함께
    assert rows[0].payload == _event("iv-1000", 1000)
    assert rows[0].meta == META
    # finalize 가 읽는다. Take 결과를 기다리는 Take 만 — 아니면 AI 를 부르지 않게 None
    assert coach_service.load_coach_events(db_session, take.id) is None
    finalizing(db_session, take)
    assert coach_service.load_coach_events(db_session, take.id) == [r.payload for r in rows]

def test_events_of_a_deleted_take_raise(db_session: Session):
    with pytest.raises(NonExistentTake):
        coach_service.append_coach_events(db_session, uuid.uuid7(), [(_event("iv-1", 1), META)])

def finalizing(db: Session, take: Take, *, consent: bool = False) -> Take:
    take.status = "ANALYZING"
    take.analysis_stage = "FINALIZING"
    take.raw_data_consent = consent
    db.add(
        TakeMetricSecond(
            take_id=take.id, t_ms=1_000, slide_number=1, gaze=None, voice=None, stt_status="ok"
        )
    )
    db.flush()
    return take
