"""Pitch 생성·수정 입력 검증 — 범위를 벗어나면 DB 에러(500) 대신 422."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.dto import PitchInfoResponseDTO
from pitch_coach_backend.module.pitch.entity import Pitch

VALID: dict[str, Any] = {"title": "발표", "time_limit_sec": 300}


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


def _add(client: TestClient, headers: dict[str, str], **override: Any):
    return client.post("/api/pitches/add", json={**VALID, **override}, headers=headers)


@pytest.mark.parametrize(
    "override",
    [
        {"title": "가" * 51},
        {"title": ""},
        {"title": "   "},
        {"title": "발\u0000표"},
        {"time_limit_sec": 0},
        {"time_limit_sec": -1},
        {"time_limit_sec": 3601},
        {"time_limit_sec": 2**31},
        {"upper_deviation": -1},
        {"upper_deviation": 301},
        {"lower_deviation": -1},
        {"lower_deviation": 2**31},
    ],
    ids=[
        "title-51",
        "title-empty",
        "title-blank",
        "title-nul",
        "time-0",
        "time-negative",
        "time-over-60min",
        "time-over-int4",
        "upper-neg",
        "upper-over-5min",
        "lower-neg",
        "lower-over-int4",
    ],
)
def test_add_rejects_out_of_range_with_422(
    client: TestClient, auth_headers: dict[str, str], override: dict[str, Any]
) -> None:
    assert _add(client, auth_headers, **override).status_code == 422


def test_add_accepts_50_char_title_and_strips_spaces(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = _add(client, auth_headers, title=f"  {'가' * 50}  ")

    assert response.status_code == 200
    pitches = client.get("/api/pitches/", headers=auth_headers).json()["pitches"]
    assert [p["pitch_title"] for p in pitches] == ["가" * 50]


def test_add_accepts_upper_bounds(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = _add(
        client, auth_headers, time_limit_sec=3600, upper_deviation=300, lower_deviation=300
    )

    assert response.status_code == 200


def test_update_rejects_title_over_50_with_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    pitch_id = _add(client, auth_headers).json()["pitch_id"]

    response = client.patch(
        f"/api/pitches/update/{pitch_id}", json={**VALID, "title": "가" * 51}, headers=auth_headers
    )

    assert response.status_code == 422


def test_get_reads_legacy_pitch_over_current_limits(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    pitch_id = _add(client, auth_headers).json()["pitch_id"]
    pitch = db_session.get(Pitch, uuid.UUID(pitch_id))
    assert pitch is not None
    pitch.title = "  예전 발표  "
    pitch.time_limit_sec = 7200
    pitch.upper_deviation = 600
    pitch.lower_deviation = 600
    db_session.flush()

    response = client.get(f"/api/pitches/{pitch_id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == PitchInfoResponseDTO(
        pitch_id=uuid.UUID(pitch_id),
        title="  예전 발표  ",
        time_limit_sec=7200,
        upper_deviation=600,
        lower_deviation=600,
        presentation_date=None,
    ).model_dump(mode="json")


def test_get_pitch_of_another_user_is_404(
    client: TestClient, auth_headers: dict[str, str], other_user_headers: dict[str, str]
) -> None:
    pitch_id = _add(client, auth_headers).json()["pitch_id"]

    response = client.get(f"/api/pitches/{pitch_id}", headers=other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"
