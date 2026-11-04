"""Take API — 새 Take 를 시작하기 전에 발표 중인 Take 묻기 (GET /takes/active)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.stream import registry
from tests.take.conftest import Versions, _make_take


def _active(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.get("/api/takes/active", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_presenting_take_is_returned_with_its_pitch(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: Versions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 3)
    take.status = "RUNNING"
    db_session.flush()
    asked: list[uuid.UUID] = []

    def blocking_take(owner_id: uuid.UUID) -> uuid.UUID:
        asked.append(owner_id)
        return take.id

    monkeypatch.setattr(registry, "blocking_take", blocking_take)

    assert _active(client, auth_headers) == {
        "take": {
            "take_id": str(take.id),
            "pitch_id": str(pitch_id),
            "pitch_title": "Take API",
            "take_number": 3,
        }
    }
    assert asked == [user_id]


def test_no_presenting_take_is_null(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "blocking_take", lambda owner_id: None)

    assert _active(client, auth_headers) == {"take": None}


def test_take_that_ended_meanwhile_is_null(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """연결은 아직 닫히는 중이어도 DB 에서 끝난 Take 는 끝낼 것이 없다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "ANALYZING"
    db_session.flush()
    monkeypatch.setattr(registry, "blocking_take", lambda owner_id: take.id)

    assert _active(client, auth_headers) == {"take": None}


def test_other_users_take_is_not_shown(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
    versions: Versions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "RUNNING"
    other = User(email="other@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    monkeypatch.setattr(registry, "blocking_take", lambda owner_id: take.id)

    headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert _active(client, headers) == {"take": None}
