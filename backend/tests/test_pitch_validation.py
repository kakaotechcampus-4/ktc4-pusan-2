"""Pitch 생성·수정 입력 검증 — 범위를 벗어나면 DB 에러(500) 대신 422."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_backend.core.security import create_access_token

VALID: dict[str, Any] = {"title": "발표", "time_limit_sec": 300}


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _add(client: TestClient, headers: dict[str, str], **override: Any):
    return client.post("/api/pitches/add", json={**VALID, **override}, headers=headers)


@pytest.mark.parametrize(
    "override",
    [
        {"title": "가" * 51},
        {"title": ""},
        {"title": "   "},
        {"time_limit_sec": 0},
        {"time_limit_sec": -1},
        {"upper_deviation": -1},
        {"lower_deviation": -1},
    ],
    ids=[
        "title-51",
        "title-empty",
        "title-blank",
        "time-0",
        "time-negative",
        "upper-neg",
        "lower-neg",
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


def test_update_rejects_title_over_50_with_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    pitch_id = _add(client, auth_headers).json()["pitch_id"]

    response = client.put(
        f"/api/pitches/update/{pitch_id}", json={**VALID, "title": "가" * 51}, headers=auth_headers
    )

    assert response.status_code == 422
