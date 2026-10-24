"""코치 연동 planner 저장 규칙 회귀 테스트."""





import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.client import PlanReply
from pitch_coach_backend.coach.requests import request_basis
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take.entity import (
    Mission,
    Take,
    TakeAIResults,
)
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

def _plan_row(pitch: Pitch, *, at: datetime, focus_area: str, **basis: Any) -> CoachingPlan:
    values = {
        "source_take_id": None,
        "script_version_id": None,
        "target_ms": 300_000,
        "min_ms": 270_000,
        "max_ms": 330_000,
        **basis,
    }
    return CoachingPlan(
        pitch_id=pitch.id,
        requested_at=at,
        payload={
            "coaching_plan": {
                "focus": [{"area": focus_area, "slide_number": 1, "weight": 1.5, "why": "근거"}],
                "relax": [],
                "max_interventions": None,
            },
            "fallback_reason": None,
            "dropped": [],
            "planner_hash": "h",
        },
        meta=META,
        **values,
    )

def test_coaching_plan_is_the_latest_request_with_the_same_basis(db_session: Session, take_parts):
    pitch, script, presentation = take_parts
    source = make_take(db_session, *take_parts, 1, status="COMPLETED", review_status="FAILED")
    source.completed_at = datetime.now(UTC)
    now = datetime.now(UTC)
    basis = {"source_take_id": source.id, "script_version_id": script.id}
    db_session.add_all(
        [
            _plan_row(pitch, at=now - timedelta(minutes=2), focus_area="GAZE", **basis),
            # 응답은 늦게 왔어도 요청은 먼저 보냈다 — 요청 시각으로 고른다
            _plan_row(pitch, at=now - timedelta(minutes=1), focus_area="FILLER", **basis),
            # 바탕 값이 다르다 (다른 Take · 다른 대본 · 다른 제한 시간)
            _plan_row(pitch, at=now, focus_area="SPEED", script_version_id=script.id),
            _plan_row(pitch, at=now, focus_area="SPEED", source_take_id=source.id),
            _plan_row(pitch, at=now, focus_area="SPEED", **basis, max_ms=360_000),
        ]
    )
    take = make_take(db_session, *take_parts, 2)

    context = coach_store.load_take_context(db_session, take.id)

    assert context is not None
    assert context.constants.coaching_plan == {
        "focus": [{"area": "FILLER", "slide_number": 1, "weight": 1.5}],
        "relax": [],
        "max_interventions": None,
    }

def test_first_take_uses_the_plan_made_without_a_source_take(db_session: Session, take_parts):
    pitch, script, presentation = take_parts
    db_session.add(
        _plan_row(pitch, at=datetime.now(UTC), focus_area="GAZE", script_version_id=script.id)
    )
    take = make_take(db_session, *take_parts, 1)

    context = coach_store.load_take_context(db_session, take.id)

    assert context is not None
    assert context.constants.coaching_plan is not None

def test_plan_request_uses_the_latest_finished_take_and_its_review(db_session: Session, take_parts):
    pitch, script, presentation = take_parts
    now = datetime.now(UTC)
    source = reviewed(
        db_session,
        make_take(db_session, *take_parts, 1),
        completed_at=now,
        recurring_issues=[{"area": "FILLER", "slide_number": None, "dimension": "fluency"}],
        overall_summary="좋아요",
    )
    db_session.add(
        TakeAIResults(take_id=source.id, kind="TAKE_RESULT", payload={"take_id": "x"}, meta=META)
    )
    row = mission(db_session, source, 1)
    mission(db_session, source, 2, status="ACHIEVED")

    request = coach_store.build_plan_request(db_session, pitch.id)

    assert request is not None
    assert request["pitch_id"] == str(pitch.id)
    assert request["source_take_id"] == str(source.id)
    assert request["script_version_id"] == str(script.id)
    assert request["plan"]["target_ms"] == 300_000
    assert [s["target_ms"] for s in request["plan"]["slides"]] == [100_000, 100_000, 100_000]
    assert request["scripts"] == [
        {"slide_number": n, "script": f"{n}번 장 대본"} for n in (1, 2, 3)
    ]
    assert [m["mission_id"] for m in request["missions"]] == [str(row.id)]
    assert request["recurring_issues"] == [
        {"area": "FILLER", "slide_number": None, "dimension": "fluency"}
    ]
    assert request["previous_take_result"] == {"take_id": "x"}
    assert request["previous_review"]["overall_summary"] == "좋아요"

def test_plan_after_a_failed_review_has_no_previous_review(db_session: Session, take_parts):
    """리뷰가 끝내 실패해도 부른다. 미션 · 반복 문제는 그 앞 리뷰의 것이다 (1-3 4번)."""
    pitch, script, presentation = take_parts
    now = datetime.now(UTC)
    older = reviewed(
        db_session,
        make_take(db_session, *take_parts, 1),
        completed_at=now - timedelta(minutes=10),
        recurring_issues=[{"area": "GAZE", "slide_number": 1, "dimension": "audience_gaze"}],
    )
    kept = mission(db_session, older, 1)
    failed = make_take(db_session, *take_parts, 2, status="COMPLETED", review_status="FAILED")
    failed.completed_at = now
    db_session.flush()

    request = coach_store.build_plan_request(db_session, pitch.id)

    assert request is not None
    assert request["source_take_id"] == str(failed.id)
    assert request["previous_review"] is None
    assert [m["mission_id"] for m in request["missions"]] == [str(kept.id)]
    assert request["recurring_issues"][0]["area"] == "GAZE"

def test_no_plan_before_the_script_is_parsed(db_session: Session, pitch: Pitch):
    db_session.add(
        ScriptVersion(pitch_id=pitch.id, version=1, content="대본", parse_status="PENDING")
    )
    db_session.flush()

    assert coach_store.build_plan_request(db_session, pitch.id) is None
    assert coach_store.build_plan_request(db_session, uuid.uuid7()) is None

PLAN_REPLY = PlanReply.model_validate(
    {
        "coaching_plan": {"focus": [], "relax": [], "max_interventions": None},
        "source_take_id": None,
        "script_version_id": None,
        "fallback_reason": "LLM_TIMEOUT",
        "dropped": [],
        "planner_hash": "h",
        "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": None},
    }
)

def test_plan_reply_is_added_as_a_row_and_chosen_for_the_next_take(db_session: Session, take_parts):
    pitch, script, presentation = take_parts
    request = coach_store.build_plan_request(db_session, pitch.id)
    assert request is not None
    requested_at = datetime.now(UTC)

    # 기본 계획(fallback_reason)도 정상 결과로 쌓는다
    assert coach_store.save_coaching_plan(db_session, pitch.id, request, PLAN_REPLY, requested_at)

    row = db_session.scalar(select(CoachingPlan).where(CoachingPlan.pitch_id == pitch.id))
    assert row is not None
    assert row.requested_at == requested_at
    assert row.payload["fallback_reason"] == "LLM_TIMEOUT"
    assert row.meta == PLAN_REPLY.meta
    basis = request_basis(request)
    assert (row.script_version_id, row.target_ms, row.min_ms, row.max_ms) == (
        script.id,
        basis.target_ms,
        basis.min_ms,
        basis.max_ms,
    )
    take = make_take(db_session, *take_parts, 1)
    context = coach_store.load_take_context(db_session, take.id)
    assert context is not None
    assert context.constants.coaching_plan == {"focus": [], "relax": [], "max_interventions": None}

def test_plan_of_a_deleted_pitch_is_not_saved(db_session: Session):
    request = {
        "pitch_id": "x",
        "source_take_id": None,
        "script_version_id": None,
        "plan": {"target_ms": 1, "min_ms": None, "max_ms": 1},
    }

    assert not coach_store.save_coaching_plan(
        db_session, uuid.uuid7(), request, PLAN_REPLY, datetime.now(UTC)
    )
