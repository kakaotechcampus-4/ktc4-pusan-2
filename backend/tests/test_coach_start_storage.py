"""코치 연동 start 저장 규칙 회귀 테스트."""





import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
    StandardAnalyses,
    Standards,
)
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.take.entity import (
    Mission,
    Take,
    TakeAIResults,
)
from pitch_coach_backend.module.take.repository import TakeRepository
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

def test_starting_a_take_fixes_missions_criteria_and_consent(db_session: Session, take_parts, user):
    pitch, script, presentation = take_parts
    now = datetime.now(UTC)
    # 리뷰가 있는 Take 중 가장 늦게 끝난 것의 미션을 싣는다. 번호 순서가 아니라 끝난 순서다
    later_finished = reviewed(
        db_session,
        make_take(db_session, *take_parts, 1),
        completed_at=now,
    )
    earlier_finished = reviewed(
        db_session,
        make_take(db_session, *take_parts, 2),
        completed_at=now - timedelta(minutes=5),
    )
    second = mission(db_session, later_finished, 2)
    first = mission(db_session, later_finished, 1)
    mission(db_session, later_finished, 3, status="ACHIEVED")
    mission(db_session, earlier_finished, 1)
    db_session.add_all(
        [
            StandardAnalyses(
                pitch_id=pitch.id, version=v, targets=[], excluded=[], meta=META, script_used=None
            )
            for v in (1, 2)
        ]
    )
    user.raw_data_consent = True
    take = make_take(db_session, *take_parts, 3)

    assert coach_service.start_take(db_session, take.id) is True

    db_session.refresh(take)
    assert take.status == "RUNNING"
    assert take.mission_ids == [str(first.id), str(second.id)]
    assert take.standard_version == 2
    assert take.raw_data_consent is True
    db_session.refresh(first)
    assert first.take_id == take.id, "판정은 take_id 가 같은 Take 의 리뷰만 덮는다"

def test_a_take_is_started_once(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1)

    assert coach_service.start_take(db_session, take.id) is True
    assert coach_service.start_take(db_session, take.id) is False, "다시 붙은 연결은 그대로 둔다"
    assert coach_service.start_take(db_session, uuid.uuid7()) is False

def test_first_take_starts_with_nothing_fixed(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1)

    coach_service.start_take(db_session, take.id)

    db_session.refresh(take)
    assert (take.mission_ids, take.standard_version, take.raw_data_consent) == ([], None, False)

@pytest.mark.parametrize("initial_version", [None, 1])
def test_review_uses_the_criteria_fixed_when_the_take_started(
    db_session: Session, take_parts, initial_version: int | None
):
    pitch, script, presentation = take_parts

    def add_criteria(version: int) -> None:
        db_session.add_all(
            [
                Standards(pitch_id=pitch.id, version=version, position=1, title=f"기준 {version}"),
                StandardAnalyses(
                    pitch_id=pitch.id,
                    version=version,
                    targets=[],
                    excluded=[],
                    meta=META,
                    script_used=(version == 1),
                ),
            ]
        )
        db_session.flush()

    if initial_version is not None:
        add_criteria(initial_version)
    take = make_take(db_session, pitch, script, presentation, 1)
    coach_service.start_take(db_session, take.id)
    add_criteria(2)
    db_session.add(TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload={"v": 1}, meta=META))
    db_session.flush()

    request = take_service.build_review_request(db_session, take.id)
    assert request.take_result == {"v": 1}
    assert request.script_coverage is None
    if initial_version is None:
        assert request.user_criteria is None
    else:
        assert request.user_criteria is not None
        assert request.user_criteria.display_criteria == ["기준 1"]
        assert request.user_criteria.script_used is True

def test_creating_a_take_leaves_missions_for_the_take_that_starts(db_session: Session, take_parts):
    """장치 점검만 하고 버린 Take 가 미션을 가져가면 다음 Take 에 실리지 않는다."""
    from pitch_coach_backend.module.take.dto import TakeInitRequestDTO

    pitch, script, presentation = take_parts
    source = reviewed(db_session, make_take(db_session, *take_parts, 1), completed_at=None)
    row = mission(db_session, source, 1)
    request = TakeInitRequestDTO(
        mode="COACHING",
        script_mode="HIGHLIGHT",
        presentation_version_id=presentation.id,
        script_version_id=script.id,
    )

    abandoned = take_service.create_take_service(db_session, pitch.id, request)
    started = take_service.create_take_service(db_session, pitch.id, request)
    coach_service.start_take(db_session, started.id)

    db_session.refresh(abandoned)
    db_session.refresh(started)
    db_session.refresh(row)
    assert abandoned.status == "FAILED"
    assert abandoned.mission_ids is None
    assert started.mission_ids == [str(row.id)]
    assert row.take_id == started.id

def test_rewriting_an_ai_result_bumps_its_revision(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="COMPLETED")
    repo = TakeRepository(db_session)

    repo.save_total_result(take.id, {"overall_summary": "처음"}, META)
    repo.save_total_result(take.id, {"overall_summary": "다시"}, META)

    row = db_session.scalar(select(TakeAIResults).where(TakeAIResults.take_id == take.id))
    db_session.refresh(row)
    assert (row.payload, row.revision) == ({"overall_summary": "다시"}, 1)

def test_completing_a_take_records_when(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="ANALYZING")

    TakeRepository(db_session).complete_take(take.id, "DONE")
    db_session.flush()

    db_session.refresh(take)
    assert take.completed_at is not None

def test_prepare_screen_shows_the_missions_take_start_assigns(db_session: Session, take_parts):
    """장치 점검에 보인 미션과 Take 를 시작할 때 싣는 미션은 같은 Take 의 같은 미션 · 같은 순서다 —
    리뷰가 늦게 끝난 Take 다 (번호 순서가 아니다)."""
    pitch, script, presentation = take_parts
    now = datetime.now(UTC)
    later = reviewed(db_session, make_take(db_session, *take_parts, 1), completed_at=now)
    earlier = reviewed(
        db_session, make_take(db_session, *take_parts, 2), completed_at=now - timedelta(minutes=1)
    )
    mission(db_session, earlier, 1)
    mission(db_session, later, 2)
    mission(db_session, later, 1)
    mission(db_session, later, 3, status="ACHIEVED")

    shown = pitch_service.get_prepare_service(db_session, pitch.id).missions
    take = make_take(db_session, *take_parts, 3)
    coach_service.start_take(db_session, take.id)

    assert shown is not None
    assert shown.source_take_number == later.take_number
    db_session.refresh(take)
    assert take.mission_ids == [str(item.mission_id) for item in shown.items]
    assert len(take.mission_ids) == 2

@pytest.mark.parametrize(
    "stage, allowed",
    [
        ("SCORING", False),
        ("AWAITING_CONFIRMATION", True),
        # 분석 단계 칸이 생기기 전에 끝난 Take. 예전처럼 리뷰를 부를 수 있다
        (None, True),
        ("FINALIZING", False),
        ("REVIEWING", False),
        ("DONE", False),
    ],
)
def test_review_starts_only_after_scoring(
    db_session: Session, take_parts, stage: str | None, allowed: bool
):
    take = make_take(db_session, *take_parts, 1, status="ANALYZING", analysis_stage=stage)

    assert TakeRepository(db_session).start_review(take.id) is allowed

def test_review_cut_off_midway_can_start_again(db_session: Session, take_parts):
    """리뷰가 도는 중에 앱이 다시 뜨면 Take 가 REVIEWING 으로 남는다. 리뷰 하나가 끝날 시간이
    지났으면 끊긴 리뷰로 보고 다시 받는다. 아직 도는 중이면 두 번 부르지 않는다."""
    now = datetime.now(UTC)
    reviewing = {"status": "ANALYZING", "analysis_stage": "REVIEWING"}
    running = make_take(db_session, *take_parts, 1, **reviewing, analysis_stage_at=now)
    cut_off = make_take(
        db_session, *take_parts, 2, **reviewing, analysis_stage_at=now - timedelta(minutes=10)
    )

    assert TakeRepository(db_session).start_review(running.id) is False
    assert TakeRepository(db_session).start_review(cut_off.id) is True
