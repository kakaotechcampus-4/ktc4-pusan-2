"""Take API — 캘리브레이션 저장."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take

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
