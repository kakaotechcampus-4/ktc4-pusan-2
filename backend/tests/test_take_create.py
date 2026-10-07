"""Take 생성(POST /api/pitches/{pitch_id}/takes/) — 버전 소속 검증과 번호 잠금."""

import threading
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take import service
from pitch_coach_backend.module.take.dto import TakeInitRequestDTO
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import TakeRepository

Versions = tuple[uuid.UUID, uuid.UUID]


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _make_pitch(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="Take 테스트", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    return pitch.id


def _make_versions(db: Session, pitch_id: uuid.UUID) -> Versions:
    presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key=f"{pitch_id}/1.pdf")
    script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본")
    db.add_all([presentation, script])
    db.flush()
    return presentation.id, script.id


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return _make_pitch(db_session, user_id)


@pytest.fixture
def versions(db_session: Session, pitch_id: uuid.UUID) -> Versions:
    return _make_versions(db_session, pitch_id)


@pytest.fixture
def other_versions(db_session: Session, user_id: uuid.UUID) -> Versions:
    """같은 사용자의 다른 pitch 에 있는 버전."""
    return _make_versions(db_session, _make_pitch(db_session, user_id))


def _create(
    client: TestClient,
    pitch_id: uuid.UUID,
    headers: dict[str, str],
    presentation_version_id: uuid.UUID,
    script_version_id: uuid.UUID,
):
    return client.post(
        f"/api/pitches/{pitch_id}/takes/",
        json={
            "mode": "PRACTICE",
            "script_mode": "FULL",
            "presentation_version_id": str(presentation_version_id),
            "script_version_id": str(script_version_id),
            "goal_time_sec": 300,
        },
        headers=headers,
    )


def _take_numbers(db: Session, pitch_id: uuid.UUID) -> list[int]:
    return list(
        db.scalars(
            select(Take.take_number).where(Take.pitch_id == pitch_id).order_by(Take.take_number)
        )
    )


def test_create_numbers_takes_in_order(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    assert _create(client, pitch_id, auth_headers, *versions).status_code == 200
    assert _create(client, pitch_id, auth_headers, *versions).status_code == 200

    assert _take_numbers(db_session, pitch_id) == [1, 2]


@pytest.mark.parametrize(
    ("swap", "code"),
    [("presentation", "PRESENTATION_VERSION_NOT_FOUND"), ("script", "SCRIPT_NOT_FOUND")],
)
def test_version_of_another_pitch_is_404(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    other_versions: Versions,
    swap: str,
    code: str,
) -> None:
    presentation_id, script_id = versions
    if swap == "presentation":
        presentation_id = other_versions[0]
    else:
        script_id = other_versions[1]

    response = _create(client, pitch_id, auth_headers, presentation_id, script_id)

    assert response.status_code == 404
    assert response.json()["code"] == code
    assert _take_numbers(db_session, pitch_id) == []


def test_unknown_version_id_is_404_not_500(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    response = _create(client, pitch_id, auth_headers, uuid.uuid4(), versions[1])

    assert response.status_code == 404
    assert response.json()["code"] == "PRESENTATION_VERSION_NOT_FOUND"


def test_concurrent_creates_on_one_pitch_get_distinct_numbers(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pitch 에 Take 둘이 동시에 와도 둘 다 성공하고 번호가 1, 2 로 갈린다.

    잠금이 없으면 둘 다 max(take_number)+1 = 1 을 받아 두 번째가 unique 위반(500)이 된다.
    트랜잭션 두 개가 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고,
    만든 데이터는 끝에 지운다.
    """
    from pitch_coach_backend.module.user.entity import User

    first_request_numbering = threading.Event()
    real_next_take_number = TakeRepository.next_take_number

    def slow_next_take_number(self: TakeRepository, pitch_id: uuid.UUID) -> int:
        # 첫 요청이 번호를 매기는 중(잠금을 쥔 채)에 두 번째 요청이 들어오게 한다
        number = real_next_take_number(self, pitch_id)
        if not first_request_numbering.is_set():
            first_request_numbering.set()
            time.sleep(0.3)
        return number

    monkeypatch.setattr(TakeRepository, "next_take_number", slow_next_take_number)

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 Take")
        setup.add(user)
        setup.flush()
        target = _make_pitch(setup, user.id)
        presentation_id, script_id = _make_versions(setup, target)
        setup.commit()
        user_id = user.id

    dto = TakeInitRequestDTO(
        mode="PRACTICE",
        script_mode="FULL",
        presentation_version_id=presentation_id,
        script_version_id=script_id,
        goal_time_sec=300,
    )
    results: dict[str, Any] = {}

    def create(name: str) -> None:
        with Session(engine, expire_on_commit=False) as db:
            try:
                take_id = service.create_take_service(db, target, dto)
                results[name] = db.get(Take, take_id).take_number
            except Exception as exc:  # 잠금이 없으면 여기로 온다
                results[name] = exc

    try:
        first = threading.Thread(target=create, args=("a",))
        first.start()
        assert first_request_numbering.wait(5)
        second = threading.Thread(target=create, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        # 잠금이 없으면 한쪽이 IntegrityError(unique 위반) 다
        assert all(isinstance(v, int) for v in results.values()), results
        assert sorted(results.values()) == [1, 2]
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # pitch·버전·take 는 CASCADE
            cleanup.commit()
