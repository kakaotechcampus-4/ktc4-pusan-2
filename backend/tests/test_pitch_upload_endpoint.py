import uuid
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.entity import PresentationVersion


@pytest.fixture(autouse=True)
def s3_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "upload", lambda file, key: key)
    monkeypatch.setattr(service, "generate_presigned_url", lambda key: f"https://fake/{key}")


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def other_user_headers(db_session: Session) -> dict[str, str]:
    from pitch_coach_backend.module.user.entity import User

    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(user)
    db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def pitch_id(client: TestClient, auth_headers: dict[str, str]) -> uuid.UUID:
    response = client.post(
        "/api/pitches/add",
        json={
            "title": "통합 테스트 발표",
            "time_limit_sec": 600,
            "presentation_date": "2026-11-01",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    return uuid.UUID(response.json()["pitch_id"])


def _files() -> dict[str, Any]:
    return {"presentation_file": ("deck.pdf", BytesIO(b"fake-pdf"), "application/pdf")}


def _upload(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str] | None = None):
    return client.post(
        f"/api/pitches/add/{pitch_id}/presentation",
        files=_files(),
        headers=headers or {},
    )


def _presentation_count(db: Session, pitch_id: uuid.UUID) -> int | None:
    return db.scalar(
        select(func.count())
        .select_from(PresentationVersion)
        .where(PresentationVersion.pitch_id == pitch_id)
    )


def test_upload_persists_presentation_version_one(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    response = _upload(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()["presentation"]

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )

    expected_key = f"pitches/{pitch_id}/presentations/1.pdf"
    assert presentation is not None
    assert presentation.version == 1
    assert presentation.file_key == expected_key
    assert body["pitch_id"] == str(pitch_id)
    assert body["file_url"] == f"https://fake/{expected_key}"


def test_second_upload_increments_version(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    assert _upload(client, pitch_id, auth_headers).status_code == 200
    response = _upload(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()["presentation"]

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )

    assert presentation is not None
    assert presentation.version == 2
    assert presentation.file_key == f"pitches/{pitch_id}/presentations/2.pdf"


def test_upload_to_another_users_pitch_is_rejected(
    client: TestClient,
    db_session: Session,
    other_user_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    response = _upload(client, pitch_id, other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"
    assert _presentation_count(db_session, pitch_id) == 0


def test_upload_without_token_is_rejected(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
) -> None:
    response = _upload(client, pitch_id)

    assert response.status_code == 401
    assert _presentation_count(db_session, pitch_id) == 0
