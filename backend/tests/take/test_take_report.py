import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.user.entity import User
from tests.take.conftest import Versions, _make_take

TAKE_RESULT_AREAS = {
    "GAZE": {
        "measured_ratio": 0.9,
        "take": {
            "audience_ratio": 0.578,
            "screen_ratio": 0.067,
            "script_ratio": 0.311,
            "away_ratio": 0.044,
        },
    },
    "SPEED": {"measured_ratio": 1.0, "take": {"cpm": 312.5}},
    "FILLER": {"measured_ratio": 1.0, "take": {"filler_count": 7}},
}


def _report(client: TestClient, take_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/takes/{take_id}/report", headers=headers)


def _add_result(db: Session, take: Take, kind: str, payload: dict) -> None:
    db.add(TakeAIResults(take_id=take.id, kind=kind, payload=payload, meta={}))
    db.flush()


def test_report_before_the_take_result_has_no_measures_or_review(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    take.analysis_stage = "FINALIZING"
    db_session.flush()

    response = _report(client, take.id, auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["take_id"] == str(take.id)
    assert body["pitch_id"] == str(pitch_id)
    assert body["pitch_title"] == "Take API"
    assert body["take_number"] == 1
    assert body["script_mode"] == "HIGHLIGHT"
    assert body["status"] == "ANALYZING"
    assert body["analysis_stage"] == "FINALIZING"
    assert body["review_status"] is None
    assert body["analysis_hold_reason"] is None
    assert body["measures"] is None
    assert body["review"] is None


def test_report_reads_measures_from_the_take_result_and_the_saved_review(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="COMPLETED")
    take.analysis_stage = "DONE"
    take.review_status = "DONE"
    review = {"take_id": str(take.id), "overall_summary": "좋았습니다", "score": 74}
    _add_result(
        db_session,
        take,
        "TAKE_RESULT",
        {"take_id": str(take.id), "duration_ms": 95_000, "areas": TAKE_RESULT_AREAS},
    )
    _add_result(db_session, take, "REVIEW", review)

    body = _report(client, take.id, auth_headers).json()

    assert body["measures"] == {
        "duration_ms": 95_000,
        "goal_ms": 300_000,
        "audience_ratio": 0.578,
        "screen_ratio": 0.067,
        "script_ratio": 0.311,
        "away_ratio": 0.044,
        "unmeasured_ms": None,
        "cpm": 312.5,
        "filler_count": 7,
    }
    assert body["review"] == review
    assert (body["status"], body["analysis_stage"], body["review_status"]) == (
        "COMPLETED",
        "DONE",
        "DONE",
    )


def test_report_leaves_unmeasured_areas_null(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    take.analysis_stage = "SCORING"
    _add_result(
        db_session,
        take,
        "TAKE_RESULT",
        {
            "take_id": str(take.id),
            "duration_ms": 60_000,
            "areas": {
                "GAZE": {"measured_ratio": 0.1, "take": None},
                "SPEED": {"take": {"cpm": None}},
            },
        },
    )

    measures = _report(client, take.id, auth_headers).json()["measures"]

    assert measures["duration_ms"] == 60_000
    assert [
        measures[key]
        for key in ("audience_ratio", "screen_ratio", "script_ratio", "away_ratio", "cpm")
    ] == [None] * 5
    assert measures["filler_count"] is None


def test_report_of_a_missing_or_someone_elses_take_is_404(
    client: TestClient,
    db_session: Session,
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.flush()
    headers = {"Authorization": f"Bearer {create_access_token(stranger.id)}"}

    for take_id in (take.id, uuid.uuid4()):
        response = _report(client, take_id, headers)
        assert response.status_code == 404
        assert response.json()["code"] == "TAKE_NOT_FOUND"
