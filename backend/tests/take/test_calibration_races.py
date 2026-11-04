"""장치 점검 저장과 Take 시작이 겹칠 때 — 기준 음량은 하나로 남는다.

트랜잭션 두 개가 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고, 만든 데이터는
끝에 지운다.
"""

import threading
import time
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.dto import CalibrationDTO
from pitch_coach_backend.module.take.entity import Calibration, Take
from pitch_coach_backend.module.take.exception import TakeAlreadyStarted
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import take as take_service

OLD_LEVEL = -30.0
NEW_LEVEL = -10.0


@pytest.fixture
def ready_take(engine: Engine) -> Generator[uuid.UUID]:
    """커밋한 `READY` Take. 앞선 점검으로 기준 음량 -30dB 가 저장돼 있다."""
    from pitch_coach_backend.module.user.entity import User

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="점검 경합")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="점검 경합", time_limit_sec=300)
        setup.add(pitch)
        setup.flush()
        presentation = PresentationVersion(
            pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"
        )
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본", parse_status="DONE")
        setup.add_all([presentation, script])
        setup.flush()
        take = Take(
            pitch_id=pitch.id,
            take_number=1,
            presentation_version_id=presentation.id,
            script_version_id=script.id,
            mode="COACHING",
            script_mode="HIGHLIGHT",
            goal_time_sec=300,
        )
        setup.add(take)
        setup.flush()
        setup.add(
            Calibration(take_id=take.id, base_level_db=OLD_LEVEL, base_volume=0, gaze_confidence=0)
        )
        setup.commit()
        user_id, take_id = user.id, take.id

    yield take_id

    with Session(engine) as cleanup:
        cleanup.delete(cleanup.get(User, user_id))  # pitch·버전·take·점검은 CASCADE
        cleanup.commit()


def _calibrate(engine: Engine, take_id: uuid.UUID, results: dict[str, Any]) -> None:
    with Session(engine, expire_on_commit=False) as db:
        results["calibration_pid"] = db.scalar(text("SELECT pg_backend_pid()"))
        # 권한 확인(`get_owned_take`)이 같은 세션에 Take 를 먼저 읽어 둔다. 세션은 객체를 약하게
        # 들고 있어 붙잡아 두지 않으면 바로 사라진다
        preloaded = db.scalar(select(Take).where(Take.id == take_id))
        assert preloaded is not None
        try:
            take_service.create_calibration_service(
                db, take_id, CalibrationDTO(base_level_db=NEW_LEVEL)
            )
            results["calibration"] = "saved"
        except Exception as exc:
            results["calibration"] = exc


def _start(engine: Engine, take_id: uuid.UUID, results: dict[str, Any]) -> None:
    """첫 실시간 연결처럼 Take 를 시작한 뒤 코치가 들고 있을 기준 음량을 읽는다."""
    with Session(engine, expire_on_commit=False) as db:
        try:
            results["start_pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            results["started"] = coach_service.start_take(db, take_id)
            values = coach_service.take_coach_values(db, take_id)
            results["coach_level"] = values.base_level_db if values is not None else None
        except Exception as exc:
            results["started"] = exc


def _stored_level(engine: Engine, take_id: uuid.UUID) -> float | None:
    with Session(engine) as db:
        return db.scalar(select(Calibration.base_level_db).where(Calibration.take_id == take_id))


def _run_in_order(
    engine: Engine,
    take_id: uuid.UUID,
    results: dict[str, Any],
    paused: threading.Event,
    release: threading.Event,
    *,
    calibration_first: bool,
) -> None:
    calibration = threading.Thread(target=_calibrate, args=(engine, take_id, results))
    start = threading.Thread(target=_start, args=(engine, take_id, results))
    first, second = (calibration, start) if calibration_first else (start, calibration)
    blocker_key, waiter_key = (
        ("calibration_pid", "start_pid") if calibration_first else ("start_pid", "calibration_pid")
    )
    first.start()
    try:
        assert paused.wait(5), results
        second.start()
        # 고정 시간만 쉬면 느린 CI 에서는 두 번째 요청이 첫 커밋 뒤에 시작해, 잠금이 없어도
        # 통과할 수 있다. DB 에서 두 번째 요청이 첫 요청을 실제로 기다리는 것을 보고 놓는다.
        deadline = time.monotonic() + 5
        with engine.connect() as observer:
            while time.monotonic() < deadline:
                waiter_pid = results.get(waiter_key)
                if waiter_pid is not None:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid}
                    )
                    if results[blocker_key] in blockers:
                        break
                time.sleep(0.01)
            else:
                pytest.fail(f"두 요청 사이의 DB 잠금 대기가 확인되지 않았다: {results}")
    finally:
        # 검증 실패 때도 잠금을 놓고 작업을 끝내야 fixture 의 데이터 정리가 기다리지 않는다.
        release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()


def test_start_during_calibration_save_uses_the_new_level(
    engine: Engine, ready_take: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """점검이 READY 를 확인한 뒤 저장하기 전에 시작이 오면, 시작은 점검이 끝날 때까지 기다리고
    새 값으로 코치를 띄운다."""
    checked = threading.Event()
    release = threading.Event()
    real_save = TakeRepository.save_calibration

    def slow_save(self: TakeRepository, calibration: Calibration) -> Calibration:
        checked.set()
        assert release.wait(10), "시작 요청의 잠금 대기를 확인하지 못했다"
        return real_save(self, calibration)

    monkeypatch.setattr(TakeRepository, "save_calibration", slow_save)
    results: dict[str, Any] = {}

    _run_in_order(
        engine,
        ready_take,
        results,
        checked,
        release,
        calibration_first=True,
    )

    assert results["calibration"] == "saved", results
    assert results["started"] is True, results
    assert results["coach_level"] == NEW_LEVEL
    assert _stored_level(engine, ready_take) == NEW_LEVEL


def test_calibration_during_start_is_rejected(
    engine: Engine, ready_take: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """시작이 RUNNING 으로 바꾼 뒤 커밋하기 전에 점검 저장이 오면, 점검은 시작이 끝날 때까지
    기다렸다가 거절된다. 코치가 읽은 값과 DB 값이 그대로 같다."""
    marked = threading.Event()
    release = threading.Event()
    real_assign = CoachRepository.assign_missions

    def slow_assign(self: CoachRepository, *args: Any, **kwargs: Any) -> list[uuid.UUID]:
        marked.set()
        assert release.wait(10), "점검 저장 요청의 잠금 대기를 확인하지 못했다"
        return real_assign(self, *args, **kwargs)

    monkeypatch.setattr(CoachRepository, "assign_missions", slow_assign)
    results: dict[str, Any] = {}

    _run_in_order(
        engine,
        ready_take,
        results,
        marked,
        release,
        calibration_first=False,
    )

    assert results["started"] is True, results
    assert isinstance(results["calibration"], TakeAlreadyStarted), results
    assert results["coach_level"] == OLD_LEVEL
    assert _stored_level(engine, ready_take) == OLD_LEVEL
