"""코치 연동 retention 저장 규칙 회귀 테스트."""



import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.entity import (
    Take,
    TakeMetricSecond,
)
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

def test_raw_data_of_failed_takes_without_consent_is_purged_after_seven_days(
    db_session: Session, take_parts
):
    now = datetime.now(UTC)
    old = now - timedelta(days=8)
    targets = {
        "expired": make_take(db_session, *take_parts, 1, status="FAILED", ended_at=old),
        "recent": make_take(db_session, *take_parts, 2, status="FAILED", ended_at=now),
        "consented": make_take(
            db_session, *take_parts, 3, status="FAILED", ended_at=old, raw_data_consent=True
        ),
        # 되살아나 다시 분석 중이다
        "revived": make_take(db_session, *take_parts, 4, status="ANALYZING", ended_at=old),
    }
    for take in targets.values():
        db_session.add(
            TakeMetricSecond(
                take_id=take.id,
                t_ms=1_000,
                slide_number=None,
                gaze=None,
                voice=None,
                stt_status="ok",
            )
        )
    db_session.flush()

    deleted = coach_service.purge_expired_raw(db_session, now)

    assert deleted == 1
    left = set(db_session.scalars(select(TakeMetricSecond.take_id)).all())
    assert targets["expired"].id not in left
    assert {targets["recent"].id, targets["consented"].id, targets["revived"].id} <= left

def test_retention_locks_only_takes_that_still_have_raw_data(db_session: Session, take_parts):
    old = datetime.now(UTC) - timedelta(days=8)
    with_raw = make_take(db_session, *take_parts, 1, status="FAILED", ended_at=old)
    # 이미 정리했거나 원자료 없이 끝난 Take
    make_take(db_session, *take_parts, 2, status="FAILED", ended_at=old)
    db_session.add(TakeMetricSecond(take_id=with_raw.id, t_ms=1_000, stt_status="ok"))
    db_session.flush()

    locked, deleted = CoachRepository(db_session).purge_expired_metric_seconds(
        datetime.now(UTC) - timedelta(days=7), None, 10
    )

    assert (locked, deleted) == ([with_raw.id], 1)

def test_retention_purges_every_batch_in_one_run(
    db_session: Session, take_parts, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(coach_service, "PURGE_BATCH_TAKES", 2)
    now = datetime.now(UTC)
    for number in range(1, 6):
        take = make_take(
            db_session, *take_parts, number, status="FAILED", ended_at=now - timedelta(days=8)
        )
        db_session.add(TakeMetricSecond(take_id=take.id, t_ms=1_000, stt_status="ok"))
    db_session.flush()

    assert coach_service.purge_expired_raw(db_session, now) == 5
    assert db_session.scalar(select(TakeMetricSecond.take_id)) is None

def test_retention_does_not_delete_raw_data_while_a_take_is_being_revived(engine):
    """Take 와 원자료를 되살리기 트랜잭션이 쥔 동안 정리가 시작된다. 옛 상태로 DELETE 를
    시작해 원자료 잠금을 기다리면, 복원 커밋 뒤에도 진행 중인 Take 의 원자료가 지워진다."""
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="보관 동시성")
        setup.add(user)
        setup.flush()
        user_id = user.id
        pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
        setup.add(pitch)
        setup.flush()
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
        presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="deck.pdf")
        setup.add_all([script, presentation])
        setup.flush()
        take = make_take(
            setup,
            pitch,
            script,
            presentation,
            1,
            status="FAILED",
            ended_at=now - timedelta(days=8),
        )
        take_id = take.id
        setup.add(TakeMetricSecond(take_id=take_id, t_ms=1_000, stt_status="ok"))
        setup.commit()

    started = threading.Event()
    worker_pid: list[int] = []

    def purge():
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            worker_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return coach_service.purge_expired_raw(db, now)

    try:
        with Session(engine) as reviving, ThreadPoolExecutor(max_workers=1) as pool:
            locked = reviving.scalar(select(Take).where(Take.id == take_id).with_for_update())
            reviving.scalar(
                select(TakeMetricSecond)
                .where(TakeMetricSecond.take_id == take_id)
                .with_for_update()
            )
            locked.status = "ANALYZING"
            locked.analysis_stage = "FINALIZING"
            reviving.flush()
            future = pool.submit(purge)
            try:
                assert started.wait(5)
                # 완료되거나 실제로 DB 잠금을 기다릴 때까지 기다린다. 스레드 실행 속도에 의존해
                # 복원 커밋을 너무 일찍 보내면 기존 DELETE 의 경쟁 조건이 재현되지 않는다.
                deadline = time.monotonic() + 5
                with engine.connect() as observer:
                    while not future.done():
                        if observer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                            {"pid": worker_pid[0]},
                        ):
                            break
                        assert time.monotonic() < deadline, "원자료 정리가 시작되지 않았다"
                        time.sleep(0.01)
            finally:
                reviving.commit()
            assert future.result(timeout=5) == 0

        with Session(engine) as verify:
            assert verify.get(Take, take_id).status == "ANALYZING"
            assert (
                verify.scalar(select(TakeMetricSecond).where(TakeMetricSecond.take_id == take_id))
                is not None
            )
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()
