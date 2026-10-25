"""시작하지 않은 채 남은 Take 정리 (`module/take/ready_sweep.py`).

FE 는 첫 실시간 연결이 ready 를 받은 뒤에 발표를 시작하고, 그 연결이 Take 를 RUNNING 으로 바꾼다.
그래서 만든 지 10분이 지나도 READY 인 Take 는 시작하지 않은 Take 다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take import ready_sweep
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service


@pytest.fixture
def make_take(db_session: Session, user_id: uuid.UUID):
    pitch = Pitch(user_id=user_id, title="정리", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="deck.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    numbers = iter(range(1, 100))

    def make(status: str, age: timedelta) -> Take:
        take = Take(
            pitch_id=pitch.id,
            take_number=next(numbers),
            presentation_version_id=presentation.id,
            script_version_id=script.id,
            mode="COACHING",
            script_mode="HIGHLIGHT",
            goal_time_sec=300,
            status=status,
            created_at=datetime.now(UTC) - age,
        )
        db_session.add(take)
        db_session.flush()
        return take

    return make


def test_only_ready_takes_older_than_the_limit_are_abandoned(db_session: Session, make_take):
    old = timedelta(minutes=11)
    stale = make_take("READY", old)
    fresh = make_take("READY", timedelta(minutes=9))
    running = make_take("RUNNING", old)
    analyzing = make_take("ANALYZING", old)

    assert lifecycle_service.abandon_stale_ready_takes(db_session) == 1

    for take in (stale, fresh, running, analyzing):
        db_session.refresh(take)
    # 끝난 시각은 알 수 없어 만든 시각으로 적는다 (새 Take 를 만들 때 정리하는 것과 같다)
    assert (stale.status, stale.end_reason, stale.ended_at) == (
        "FAILED",
        "ABANDONED",
        stale.created_at,
    )
    assert [t.status for t in (fresh, running, analyzing)] == ["READY", "RUNNING", "ANALYZING"]


def test_a_take_abandoned_before_its_connection_cannot_start(db_session: Session, make_take):
    """정리와 첫 연결은 둘 다 조건부 UPDATE 라 먼저 적은 쪽이 남는다."""
    stale = make_take("READY", timedelta(minutes=11))

    lifecycle_service.abandon_stale_ready_takes(db_session)

    assert lifecycle_service.mark_running(db_session, stale.id) is False
    db_session.refresh(stale)
    assert stale.status == "FAILED"


@pytest.mark.anyio
async def test_sweep_once_reports_and_survives_a_failure(db_session: Session, make_take):
    make_take("READY", timedelta(minutes=11))

    assert await ready_sweep.sweep_once(lambda: db_session) == 1
    assert await ready_sweep.sweep_once(lambda: db_session) == 0

    def broken() -> Session:
        raise RuntimeError("db down")

    # 다음 주기에 다시 한다. 앱은 멈추지 않는다
    assert await ready_sweep.sweep_once(broken) == 0
