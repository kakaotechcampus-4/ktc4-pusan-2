"""발표를 지워도 되는지는 BE 가 정한다. 삭제와 홈 목록이 같은 판단을 쓴다."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from pitch_coach_backend.module.admin import service as admin_service
from pitch_coach_backend.module.pitch.entity import (
    PendingFileDeletion,
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.exception import PitchBusy
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import file_cleanup
from pitch_coach_backend.module.pitch.service import pitch as pitch_service
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service import lookup as take_lookup
from pitch_coach_backend.module.user.entity import User

HELD = {"analysis_hold_reason": "COVERAGE_RESCORE_FAILED"}


def _ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _analyzing(stage: str = "SCORING", minutes_ago: float | None = 1, **fields: Any) -> dict:
    return {
        "status": "ANALYZING",
        "analysis_stage": stage,
        "analysis_stage_at": None if minutes_ago is None else _ago(minutes_ago),
        **fields,
    }


# 멈춘 분석 기준: 단계 시각 뒤 15분. 확인 답을 기다리는 단계는 10분 더 기다린다
BUSY = {
    "발표 중": {"status": "RUNNING"},
    "분석 중": _analyzing(),
    "기준 직전까지 분석 중": _analyzing(minutes_ago=14),
    "확인 답 대기": _analyzing("AWAITING_CONFIRMATION", 24),
}
FREE = {
    "시작 전": {"status": "READY"},
    "완료": {"status": "COMPLETED"},
    "실패": {"status": "FAILED"},
    "보류": _analyzing(**HELD),
    "단계 시각 없음": _analyzing(minutes_ago=None),
    "멈춘 분석": _analyzing(minutes_ago=16),
    "확인 답 대기가 멈춤": _analyzing("AWAITING_CONFIRMATION", 26),
}


def _add_pitch(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    db.add_all(
        [
            PresentationVersion(pitch_id=pitch.id, version=1, file_key="k"),
            ScriptVersion(
                pitch_id=pitch.id, version=1, content="대본", parse_status=ScriptParseStatus.DONE
            ),
        ]
    )
    db.flush()
    return pitch.id


def _add_take(db: Session, pitch_id: uuid.UUID, number: int = 1, **fields: Any) -> Take:
    presentation = db.query(PresentationVersion).filter_by(pitch_id=pitch_id).one()
    script = db.query(ScriptVersion).filter_by(pitch_id=pitch_id).one()
    take = Take(
        pitch_id=pitch_id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        **fields,
    )
    db.add(take)
    db.flush()
    return take


@pytest.mark.parametrize("fields", BUSY.values(), ids=BUSY.keys())
def test_pitch_with_a_working_take_is_not_deleted(
    db_session: Session, user_id: uuid.UUID, fields: dict
) -> None:
    pitch_id = _add_pitch(db_session, user_id)
    _add_take(db_session, pitch_id, **fields)

    with pytest.raises(PitchBusy):
        pitch_service.delete_pitch_service(db_session, pitch_id)

    assert db_session.get(Pitch, pitch_id) is not None
    assert db_session.get(PendingFileDeletion, file_cleanup.pitch_prefix(pitch_id)) is None


@pytest.mark.parametrize("fields", FREE.values(), ids=FREE.keys())
def test_pitch_without_a_working_take_is_deleted(
    db_session: Session, user_id: uuid.UUID, fields: dict
) -> None:
    pitch_id = _add_pitch(db_session, user_id)
    take_id = _add_take(db_session, pitch_id, **fields).id

    assert pitch_service.delete_pitch_service(db_session, pitch_id) == [take_id]


def test_one_working_take_among_finished_ones_blocks_the_pitch(
    db_session: Session, user_id: uuid.UUID
) -> None:
    pitch_id = _add_pitch(db_session, user_id)
    _add_take(db_session, pitch_id, 1, status="COMPLETED")
    _add_take(db_session, pitch_id, 2, status="RUNNING")

    with pytest.raises(PitchBusy):
        pitch_service.delete_pitch_service(db_session, pitch_id)


def test_delete_api_answers_409_with_the_code_and_keeps_the_pitch(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch_id = _add_pitch(db_session, user_id)
    _add_take(db_session, pitch_id, status="RUNNING")

    response = client.delete(f"/api/pitches/delete/{pitch_id}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "PITCH_BUSY"
    assert db_session.get(Pitch, pitch_id) is not None


def test_home_list_tells_the_same_answer_as_delete(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    expected = {}
    for answer, cases in ((False, BUSY), (True, FREE)):
        for fields in cases.values():
            pitch_id = _add_pitch(db_session, user_id)
            _add_take(db_session, pitch_id, **fields)
            expected[str(pitch_id)] = answer
    empty = _add_pitch(db_session, user_id)
    expected[str(empty)] = True

    response = client.get("/api/pitches/", headers=auth_headers)

    assert response.status_code == 200
    assert {p["pitch_id"]: p["can_delete"] for p in response.json()["pitches"]} == expected


def test_stalled_analysis_means_the_same_as_the_admin_screen(
    db_session: Session, user_id: uuid.UUID
) -> None:
    """운영 화면의 "멈춘 분석" 은 SQL 로, 삭제 판단은 코드로 잰다. 기준이 어긋나면 여기서 걸린다."""
    pitch_id = _add_pitch(db_session, user_id)
    analyzing = [
        _add_take(db_session, pitch_id, number, **fields)
        for number, fields in enumerate(
            (
                fields
                for fields in (*BUSY.values(), *FREE.values())
                if fields["status"] == "ANALYZING"
            ),
            start=1,
        )
    ]
    now = datetime.now(UTC)

    on_admin_screen = {row.id for row in admin_service.list_stalled_analyses(db_session, now)}

    for take in analyzing:
        assert (take.id in on_admin_screen) is not take_lookup.blocks_pitch_deletion(take, now)


# ── 동시에 온 삭제와 Take 시작. 실제 커밋이 필요해 저장점 세션을 쓰지 않는다 ─────────────


@pytest.fixture
def committed_take(engine: Engine) -> Any:
    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="경합")
        setup.add(user)
        setup.flush()
        pitch_id = _add_pitch(setup, user.id)
        take_id = _add_take(setup, pitch_id).id
        user_id = user.id
        setup.commit()
    yield pitch_id, take_id
    with Session(engine) as cleanup:
        cleanup.delete(cleanup.get(User, user_id))
        cleanup.execute(
            delete(PendingFileDeletion).where(
                PendingFileDeletion.prefix == file_cleanup.pitch_prefix(pitch_id)
            )
        )
        cleanup.commit()


def test_delete_waits_for_a_starting_take_and_then_refuses(
    engine: Engine, committed_take: tuple[uuid.UUID, uuid.UUID]
) -> None:
    pitch_id, take_id = committed_take
    outcome: list[object] = []

    def delete_pitch() -> None:
        with Session(engine) as deleter:
            try:
                outcome.append(pitch_service.delete_pitch_service(deleter, pitch_id))
            except PitchBusy as error:
                outcome.append(error)

    with Session(engine) as starter:
        # `coach_service.start_take` 가 커밋하기 직전과 같다
        PitchRepository(starter).lock_for_new_version(pitch_id)
        assert CoachRepository(starter).mark_started(take_id) is not None
        thread = threading.Thread(target=delete_pitch)
        thread.start()
        time.sleep(0.2)
        assert thread.is_alive(), "시작이 커밋할 때까지 기다려야 한다"
        starter.commit()

    thread.join(timeout=5)
    assert len(outcome) == 1 and isinstance(outcome[0], PitchBusy)
    with Session(engine) as check:
        assert check.get(Pitch, pitch_id) is not None


def test_start_waits_for_a_delete_and_then_finds_nothing(
    engine: Engine, committed_take: tuple[uuid.UUID, uuid.UUID]
) -> None:
    pitch_id, take_id = committed_take
    outcome: list[bool] = []

    def start_take() -> None:
        with Session(engine) as starter:
            outcome.append(coach_service.start_take(starter, take_id))

    with Session(engine) as deleter:
        assert PitchRepository(deleter).lock_for_delete(pitch_id) is not None
        thread = threading.Thread(target=start_take)
        thread.start()
        time.sleep(0.2)
        assert thread.is_alive(), "삭제가 커밋할 때까지 기다려야 한다"
        assert pitch_service.delete_pitch_service(deleter, pitch_id) == [take_id]

    thread.join(timeout=5)
    assert outcome == [False]
    with Session(engine) as check:
        assert check.get(Pitch, pitch_id) is None
