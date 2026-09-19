import uuid
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.entity import PresentationVersion, ScriptVersion

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def upload_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "upload", lambda file, key: key)


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
    return {
        "presentation_file": ("deck.pdf", BytesIO(b"fake-pdf"), "application/pdf"),
        "script_file": ("script.docx", BytesIO(b"fake-docx"), DOCX),
    }


def _upload(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.post(
        f"/api/pitches/{pitch_id}/upload",
        files=_files(),
        data={"description": "초안"},
        headers=headers,
    )


def test_upload_persists_presentation_and_script_in_one_transaction(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    response = _upload(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )
    script = db_session.get(ScriptVersion, uuid.UUID(body["script_version_id"]))

    assert presentation is not None
    assert script is not None
    assert presentation.version == 1
    assert script.version == 1
    assert presentation.file_key == f"pitches/{pitch_id}/presentations/1.pdf"
    assert script.file_key == f"pitches/{pitch_id}/scripts/1.docx"
    assert presentation.description == "초안"


def test_second_upload_increments_both_versions(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    assert _upload(client, pitch_id, auth_headers).status_code == 200
    response = _upload(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()

    presentation = db_session.get(
        PresentationVersion, uuid.UUID(body["presentation_version_id"])
    )
    script = db_session.get(ScriptVersion, uuid.UUID(body["script_version_id"]))

    assert presentation is not None
    assert script is not None
    assert presentation.version == 2
    assert script.version == 2
    assert presentation.file_key == f"pitches/{pitch_id}/presentations/2.pdf"
    assert script.file_key == f"pitches/{pitch_id}/scripts/2.docx"


def test_upload_to_another_users_pitch_is_rejected(
    client: TestClient,
    db_session: Session,
    other_user_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    response = _upload(client, pitch_id, other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"

    presentations = db_session.scalar(
        select(func.count())
        .select_from(PresentationVersion)
        .where(PresentationVersion.pitch_id == pitch_id)
    )
    scripts = db_session.scalar(
        select(func.count()).select_from(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)
    )

    assert presentations == 0
    assert scripts == 0


def test_upload_without_token_is_rejected(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
) -> None:
    response = client.post(
        f"/api/pitches/{pitch_id}/upload",
        files=_files(),
        data={"description": "초안"},
    )

    assert response.status_code == 401

    presentations = db_session.scalar(
        select(func.count())
        .select_from(PresentationVersion)
        .where(PresentationVersion.pitch_id == pitch_id)
    )

    assert presentations == 0
