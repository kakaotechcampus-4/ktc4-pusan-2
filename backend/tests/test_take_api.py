"""Take API — 이전 미션 조회, 부분 수정(PUT), 입력 검증."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Mission, Take

Versions = tuple[uuid.UUID, uuid.UUID]


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="Take API", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    return pitch.id


@pytest.fixture
def versions(db_session: Session, pitch_id: uuid.UUID) -> Versions:
    presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key=f"{pitch_id}/1.pdf")
    script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    return presentation.id, script.id


def _make_take(db: Session, pitch_id: uuid.UUID, versions: Versions, take_number: int) -> Take:
    take = Take(
        pitch_id=pitch_id,
        take_number=take_number,
        presentation_version_id=versions[0],
        script_version_id=versions[1],
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db.add(take)
    db.flush()
    return take


def _missions(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/pitches/{pitch_id}/takes/previous-missions", headers=headers)


# ── 이전 미션 ──────────────────────────────────────────────────────


def test_previous_missions_without_take_is_empty(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["missions"] == {
        "source_take_id": None,
        "next_take_number": 1,
        "missions": [],
    }


def test_previous_missions_come_from_latest_take(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    old = _make_take(db_session, pitch_id, versions, 1)
    latest = _make_take(db_session, pitch_id, versions, 2)
    db_session.add(Mission(source_take_id=old.id, slide_number=1, description="옛것", priority=1))
    second = Mission(source_take_id=latest.id, slide_number=3, description="시선", priority=2)
    first = Mission(source_take_id=latest.id, slide_number=5, description="속도", priority=1)
    db_session.add_all([second, first])
    db_session.flush()

    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()["missions"]
    assert body["source_take_id"] == str(latest.id)
    assert body["next_take_number"] == 3
    assert body["missions"] == [
        {
            "mission_id": str(first.id),
            "slide_number": 5,
            "description": "속도",
            "priority": 1,
            "completed": False,
        },
        {
            "mission_id": str(second.id),
            "slide_number": 3,
            "description": "시선",
            "priority": 2,
            "completed": False,
        },
    ]


def test_previous_missions_of_take_without_missions_is_empty_list(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    _make_take(db_session, pitch_id, versions, 1)

    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["missions"]["missions"] == []


# ── PUT /takes/{take_id} ────────────────────────────────────────────


def _put(client: TestClient, take: Take, headers: dict[str, str], body: dict):
    return client.put(f"/api/pitches/{take.pitch_id}/takes/{take.id}", json=body, headers=headers)


def test_put_keeps_fields_that_were_not_sent(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    started = "2026-10-07T10:00:00+09:00"
    ended = "2026-10-07T10:05:00+09:00"
    logs = [{"type": "slide", "at_ms": 0}]

    assert _put(client, take, auth_headers, {"started_at": started, "event_logs": logs}).is_success
    assert _put(client, take, auth_headers, {"ended_at": ended}).is_success

    db_session.refresh(take)
    assert take.started_at == datetime.fromisoformat(started)
    assert take.ended_at == datetime.fromisoformat(ended)
    assert take.event_logs == logs


def test_put_with_bad_datetime_is_422_not_500(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = _put(client, take, auth_headers, {"started_at": "어제 저녁"})

    assert response.status_code == 422


def test_put_accepts_utc_z_suffix(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    assert _put(client, take, auth_headers, {"ended_at": "2026-10-07T01:05:00Z"}).is_success

    db_session.refresh(take)
    assert take.ended_at == datetime(2026, 10, 7, 1, 5, tzinfo=UTC)


# ── 입력 검증 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("goal_time_sec", [0, -60])
def test_create_with_non_positive_goal_time_is_422(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    goal_time_sec: int,
) -> None:
    response = client.post(
        f"/api/pitches/{pitch_id}/takes/",
        json={
            "mode": "COACHING",
            "script_mode": "HIGHLIGHT",
            "presentation_version_id": str(versions[0]),
            "script_version_id": str(versions[1]),
            "goal_time_sec": goal_time_sec,
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(("base_volume", "status"), [(999.99, 200), (1000, 422), (-1, 422)])
def test_calibration_base_volume_range(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    base_volume: float,
    status: int,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        json={"base_volume": base_volume},
        headers=auth_headers,
    )

    assert response.status_code == status


def test_calibration_is_saved(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """gaze_confidence 를 bool 로 받던 때는 Float 컬럼에 넣다가 항상 500 이었다."""
    from pitch_coach_backend.module.take.entity import Calibration

    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        json={"face_detected": True, "base_volume": 42.5, "gaze_confidence": 0.82},
        headers=auth_headers,
    )

    assert response.status_code == 200
    saved = db_session.get(Calibration, uuid.UUID(response.json()["calibration_id"]))
    assert saved is not None
    assert (saved.face_detected, float(saved.base_volume), saved.gaze_confidence) == (
        True,
        42.5,
        0.82,
    )
