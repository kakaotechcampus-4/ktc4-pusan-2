"""사용자 테스트 참여 동의 API 회귀 테스트."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.user.entity import User


@pytest.fixture
def headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_not_consented_until_the_user_agrees(client: TestClient, headers: dict[str, str]):
    me = client.get("/api/users/me", headers=headers).json()
    assert (me["test_consent_at"], me["test_consent_version"]) == (None, None)

    agreed = client.patch("/api/users/me", json={"test_consent_version": 1}, headers=headers)

    assert agreed.status_code == 200
    assert agreed.json()["test_consent_version"] == 1
    assert agreed.json()["test_consent_at"] is not None
    assert agreed.json()["raw_data_consent"] is False


def test_same_version_keeps_first_time_and_new_version_renews(
    client: TestClient, headers: dict[str, str], db_session: Session, user_id: uuid.UUID
):
    past = datetime(2026, 10, 1, tzinfo=UTC)
    user = db_session.get(User, user_id)
    user.test_consent_version, user.test_consent_at = 1, past
    db_session.flush()

    again = client.patch("/api/users/me", json={"test_consent_version": 1}, headers=headers)
    assert datetime.fromisoformat(again.json()["test_consent_at"]) == past

    renewed = client.patch("/api/users/me", json={"test_consent_version": 2}, headers=headers)
    assert renewed.json()["test_consent_version"] == 2
    assert datetime.fromisoformat(renewed.json()["test_consent_at"]) > past

    unchanged = client.patch("/api/users/me", json={"raw_data_consent": True}, headers=headers)
    assert unchanged.json()["test_consent_version"] == 2


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True])
def test_invalid_version_is_422(client: TestClient, headers: dict[str, str], version):
    response = client.patch(
        "/api/users/me", json={"test_consent_version": version}, headers=headers
    )
    assert response.status_code == 422


def test_db_keeps_time_and_version_together(db_session: Session, user_id: uuid.UUID):
    user = db_session.get(User, user_id)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        user.test_consent_version = 1
        db_session.flush()
