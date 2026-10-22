"""장치 점검 준비 정보(GET /api/pitches/{pitch_id}/prepare)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take


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
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="캡스톤 최종 발표", time_limit_sec=600)
    db_session.add(pitch)
    db_session.flush()
    return pitch.id


def _presentation(db: Session, pitch_id: uuid.UUID, version: int) -> PresentationVersion:
    row = PresentationVersion(
        pitch_id=pitch_id, version=version, file_key=f"{pitch_id}/{version}.pdf"
    )
    db.add(row)
    db.flush()
    return row


def _script(db: Session, pitch_id: uuid.UUID, version: int, parse_status: str) -> ScriptVersion:
    row = ScriptVersion(
        pitch_id=pitch_id, version=version, content="대본", parse_status=parse_status
    )
    db.add(row)
    db.flush()
    return row


def _take(
    db: Session,
    pitch_id: uuid.UUID,
    take_number: int,
    script_mode: str,
    presentation: PresentationVersion,
    script: ScriptVersion,
) -> None:
    db.add(
        Take(
            pitch_id=pitch_id,
            take_number=take_number,
            presentation_version_id=presentation.id,
            script_version_id=script.id,
            mode="COACHING",
            script_mode=script_mode,
            goal_time_sec=600,
        )
    )
    db.flush()


def _prepare(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/pitches/{pitch_id}/prepare", headers=headers)


def test_new_pitch_has_no_versions_and_starts_at_take_1(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _prepare(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "pitch_id": str(pitch_id),
        "title": "캡스톤 최종 발표",
        "time_limit_sec": 600,
        "presentation": None,
        "script": None,
        "newer_script": None,
        "next_take_number": 1,
        "default_script_mode": "HIGHLIGHT",
    }


def test_picks_latest_presentation_and_latest_parsed_script(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    _presentation(db_session, pitch_id, 1)
    latest_presentation = _presentation(db_session, pitch_id, 2)
    _script(db_session, pitch_id, 1, "DONE")
    parsed = _script(db_session, pitch_id, 2, "DONE")

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["presentation"] == {"id": str(latest_presentation.id), "version": 2}
    assert body["script"] == {"id": str(parsed.id), "version": 2}
    assert body["newer_script"] is None


def test_skips_scripts_not_parsed_and_reports_the_newest(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parsed = _script(db_session, pitch_id, 1, "DONE")
    _script(db_session, pitch_id, 2, "FAILED")
    pending = _script(db_session, pitch_id, 3, "PENDING")

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["script"] == {"id": str(parsed.id), "version": 1}
    assert body["newer_script"] == {
        "id": str(pending.id),
        "version": 3,
        "parse_status": "PENDING",
        "error_code": None,
    }


def test_newer_script_without_any_parsed_one_is_reported(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    failed = _script(db_session, pitch_id, 1, "FAILED")
    failed.parse_error = "AI_TIMEOUT"
    db_session.flush()

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["script"] is None
    assert body["newer_script"] == {
        "id": str(failed.id),
        "version": 1,
        "parse_status": "FAILED",
        "error_code": "AI_TIMEOUT",
    }


def test_stale_pending_script_is_reported_as_expired(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """BE 가 재시작돼 끝나지 못한 파싱. 대본 조회(GET /scripts)와 같은 기준으로 FAILED 로 본다."""
    stale = _script(db_session, pitch_id, 1, "PENDING")
    stale.parse_requested_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["newer_script"]["parse_status"] == "FAILED"
    assert body["newer_script"]["error_code"] == "PARSE_EXPIRED"


def test_next_take_number_and_script_mode_follow_latest_take(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    presentation = _presentation(db_session, pitch_id, 1)
    script = _script(db_session, pitch_id, 1, "DONE")
    _take(db_session, pitch_id, 1, "HIGHLIGHT", presentation, script)
    _take(db_session, pitch_id, 2, "KEYWORD", presentation, script)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["next_take_number"] == 3
    assert body["default_script_mode"] == "KEYWORD"


def test_pitch_of_another_user_is_404(
    client: TestClient, other_user_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _prepare(client, pitch_id, other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"
