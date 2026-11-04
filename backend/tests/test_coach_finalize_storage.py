"""코치 연동 finalize 저장 규칙 회귀 테스트."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.replies import FinalizeReply
from pitch_coach_backend.coach.requests import SlideEvent
from pitch_coach_backend.coach.store import DbCoachStore
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.entity import (
    CoachEvent,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
    TakeSummary,
)
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import record as record_service
from pitch_coach_backend.module.user.entity import User

EXAMPLES = (
    Path(__file__).resolve().parents[2] / "ai/service/src/pitch_coach_ai/features/coach/examples"
)

META = {"schema_version": "1.0", "feature_version": "coach-1.2", "model": None}


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


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


def _event(event_id: str, t_ms: int, kind: str = "INTERVENTION") -> dict[str, Any]:
    return {"kind": kind, "event_id": event_id, "t_ms": t_ms, "evidence": {"start_ms": 0}}


def finalize_reply(take: Take, *, duration_ms: int = 92_400) -> FinalizeReply:
    body = copy.deepcopy(example("finalize/response.json"))
    body["take_result"]["take_id"] = str(take.id)
    body["take_result"]["duration_ms"] = duration_ms
    return FinalizeReply.model_validate(body)


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


def test_take_result_is_saved_in_one_transaction(db_session: Session, take_parts):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1))
    reply = finalize_reply(take)
    unsaved = [(_event("iv-500", 500), META)]

    assert coach_store.save_take_result(db_session, take.id, reply, unsaved) is True

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "SCORING")
    assert take.duration_sec == 92
    result = db_session.scalar(
        select(TakeAIResults).where(
            TakeAIResults.take_id == take.id, TakeAIResults.kind == "TAKE_RESULT"
        )
    )
    assert result is not None
    assert result.payload == reply.take_result
    assert result.meta == reply.meta
    assert result.revision == 0
    summary = db_session.get(TakeSummary, take.id)
    assert summary is not None
    assert (summary.average_cpm, summary.filler_count, summary.overall_confidence) == (300.0, 2, 99)
    events = db_session.scalars(
        select(CoachEvent.event_id).where(CoachEvent.take_id == take.id).order_by(CoachEvent.seq)
    ).all()
    assert events == ["iv-500", *[e["event_id"] for e in reply.events]]
    # 동의 없는 Take 는 Take 결과가 생기면 1초 원자료를 지운다 (replay 에 쓸 일이 없다)
    assert (
        db_session.scalars(
            select(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id)
        ).all()
        == []
    )


def test_consented_take_keeps_the_raw_data(db_session: Session, take_parts):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1), consent=True)

    coach_store.save_take_result(db_session, take.id, finalize_reply(take), [])

    rows = db_session.scalars(
        select(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id)
    ).all()
    assert len(rows) == 1


def test_take_result_keeps_the_review_columns(db_session: Session, take_parts):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1))
    db_session.add(TakeSummary(take_id=take.id, summary="리뷰", score=80, speak_accuracy=70))
    db_session.flush()

    coach_store.save_take_result(db_session, take.id, finalize_reply(take), [])

    summary = db_session.get(TakeSummary, take.id)
    db_session.refresh(summary)
    assert (summary.summary, summary.score, summary.speak_accuracy) == ("리뷰", 80, 70)
    assert summary.average_cpm == 300.0


@pytest.mark.parametrize(
    "status, stage",
    [("ANALYZING", "SCORING"), ("COMPLETED", "DONE"), ("FAILED", "FINALIZING"), ("RUNNING", None)],
)
def test_take_result_is_dropped_unless_the_take_waits_for_it(
    db_session: Session, take_parts, status: str, stage: str | None
):
    """재시작한 BE 와 앞 작업의 응답이 겹쳐도 이미 앞으로 간 단계를 되돌리지 않는다 (12-2)."""
    take = make_take(db_session, *take_parts, 1, status=status, analysis_stage=stage)
    # 버릴 때 롤백한다. 만든 행이 savepoint 안에 남게 커밋해 둔다
    db_session.commit()

    assert coach_store.save_take_result(db_session, take.id, finalize_reply(take), []) is False

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == (status, stage)
    assert (
        db_session.scalars(select(TakeAIResults).where(TakeAIResults.take_id == take.id)).all()
        == []
    )


def test_failed_finalize_marks_the_take_failed_and_keeps_the_raw_data(
    db_session: Session, take_parts
):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1))

    assert coach_service.fail_take_result(db_session, take.id) is True
    assert coach_service.fail_take_result(db_session, take.id) is False

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("FAILED", "FINALIZING")
    assert (
        len(
            db_session.scalars(
                select(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id)
            ).all()
        )
        == 1
    )


def test_finalize_source_has_the_raw_data(db_session: Session, take_parts):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1, utterance_ends=[900]))
    db_session.add(TakeSlideEvent(take_id=take.id, slide_number=2, started_ms=500))
    coach_service.append_coach_events(db_session, take.id, [(_event("iv-1", 1), META)])

    source = coach_store.load_finalize_source(db_session, take.id)

    assert source is not None
    assert source.t_end_ms == 1_000
    assert source.utterance_ends == (900,)
    assert source.events == (_event("iv-1", 1),)
    assert source.context.slides == (SlideEvent(2, 500),)
    assert source.replay(1_000)["slides"] == [{"number": 2, "started_ms": 0}]


def test_finalize_source_is_only_for_a_take_waiting_for_its_result(db_session: Session, take_parts):
    take = make_take(db_session, *take_parts, 1, status="RUNNING")

    assert coach_store.load_finalize_source(db_session, take.id) is None
    assert coach_store.load_finalize_source(db_session, uuid.uuid7()) is None


def test_finalizing_takes_are_found_for_the_restart(db_session: Session, take_parts):
    waiting = finalizing(db_session, make_take(db_session, *take_parts, 1))
    make_take(db_session, *take_parts, 2, status="ANALYZING", analysis_stage="SCORING")

    assert waiting.id in coach_service.finalizing_takes(db_session)
    assert len([t for t in coach_service.finalizing_takes(db_session) if t == waiting.id]) == 1


def test_only_takes_left_waiting_long_are_picked_up_again(db_session: Session, take_parts):
    """막 끝낸 Take 는 그 Take 의 finalize 가 곧 뜬다. 오래 남은 것만 다시 띄운다. 기다리기 시작한
    시각이 없으면(칸이 생기기 전) 오래된 것으로 본다."""
    now = datetime.now(UTC)
    fresh = finalizing(db_session, make_take(db_session, *take_parts, 1, analysis_stage_at=now))
    old = finalizing(
        db_session,
        make_take(db_session, *take_parts, 2, analysis_stage_at=now - timedelta(hours=1)),
    )
    unknown = finalizing(db_session, make_take(db_session, *take_parts, 3))

    found = coach_service.finalizing_takes(db_session, now - timedelta(minutes=10))

    assert old.id in found and unknown.id in found
    assert fresh.id not in found


@pytest.mark.anyio
async def test_db_store_opens_a_session_for_each_call(db_session: Session, take_parts):
    take = finalizing(db_session, make_take(db_session, *take_parts, 1))
    store = DbCoachStore(session_factory=lambda: db_session)

    await store.append_events(take.id, [(_event("iv-1", 1), META)])

    assert await store.load_events(take.id) == [_event("iv-1", 1)]
    assert await store.finalizing_takes() == [take.id]
    source = await store.load_finalize_source(take.id)
    assert source is not None and source.t_end_ms == 1_000
    assert await store.fail_take_result(take.id) is True


@pytest.mark.parametrize(
    "status, stage, saved",
    [
        ("RUNNING", None, True),
        ("ANALYZING", "FINALIZING", True),
        ("ANALYZING", "SCORING", False),
        ("COMPLETED", "DONE", False),
        # Take 결과를 만들지 못한 Take · 떠난 Take. replay 재료라 늦게 온 행도 둔다 (7일 뒤 지운다)
        ("FAILED", "FINALIZING", True),
        ("FAILED", None, True),
    ],
)
def test_raw_seconds_are_not_added_after_the_take_result(
    db_session: Session, take_parts, status: str, stage: str | None, saved: bool
):
    """Take 결과를 저장할 때 동의 없는 Take 의 1초 원자료를 지운다. 그 뒤에 늦게 들어간 행은 끝내
    남는다 (늦게 커밋된 저장 · 다시 넣기). Take 결과가 없는 Take 는 받는다."""
    from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO

    take = make_take(db_session, *take_parts, 1, status=status, analysis_stage=stage)
    db_session.commit()
    row = MetricSecondCreateDTO(
        t_ms=1_000, slide_number=None, gaze=None, voice=None, stt_status="ok"
    )

    record_service.append_metric_seconds(db_session, take.id, [row])

    rows = db_session.scalars(
        select(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id)
    ).all()
    assert len(rows) == (1 if saved else 0)


def test_raw_seconds_of_a_deleted_take_raise(db_session: Session):
    from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO

    row = MetricSecondCreateDTO(
        t_ms=1_000, slide_number=None, gaze=None, voice=None, stt_status="ok"
    )

    with pytest.raises(NonExistentTake):
        record_service.append_metric_seconds(db_session, uuid.uuid7(), [row])


@pytest.mark.anyio
async def test_finalizing_ids_are_batched_without_skipping_after_completion(db_session, take_parts):
    waiting = [finalizing(db_session, make_take(db_session, *take_parts, n)) for n in range(1, 7)]
    ids = sorted(take.id for take in waiting)
    db_session.commit()
    store = DbCoachStore(session_factory=lambda: db_session)
    first = await store.finalizing_takes(limit=2)
    assert first == ids[:2]
    # 앞 배치가 상태 목록에서 빠져도 커서 뒤의 Take 를 빠뜨리지 않는다.
    for take_id in first:
        coach_service.fail_take_result(db_session, take_id)
    second = await store.finalizing_takes(after=first[-1], limit=2)
    third = await store.finalizing_takes(after=second[-1], limit=2)
    assert second + third == ids[2:]
    assert await store.finalizing_takes(after=third[-1], limit=2) == []
