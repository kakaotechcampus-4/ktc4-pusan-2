"""Take 생성(POST /api/pitches/{pitch_id}/takes/) — 버전 소속 검증."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take

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

