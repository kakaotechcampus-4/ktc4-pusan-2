"""원자료 보관 동의 API 회귀 테스트."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.user.entity import User


@pytest.fixture
def headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}

def test_consent_is_off_until_the_user_turns_it_on(
    client: TestClient, headers: dict[str, str], db_session: Session, user_id: uuid.UUID
):
    assert client.get("/api/users/me", headers=headers).json()["raw_data_consent"] is False

    response = client.patch("/api/users/me", json={"raw_data_consent": True}, headers=headers)

    assert response.status_code == 200
    assert response.json()["raw_data_consent"] is True
    user = db_session.get(User, user_id)
    db_session.refresh(user)
    assert user.raw_data_consent is True

def test_empty_settings_change_nothing(client: TestClient, headers: dict[str, str]):
    response = client.patch("/api/users/me", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["raw_data_consent"] is False
