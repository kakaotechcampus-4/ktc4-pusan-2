import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.coach.replies import summary_measures
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.user.entity import User
from tests.take.conftest import Versions, _make_take
from tests.take.coverage_fixtures import CRITERIA_VERSION, META, TAKE_DETAIL, all_failed, scored

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
    assert body["content_status"] == {
        "state": "ANALYZING",
        "reason_code": None,
        "revision": None,
        "criteria_version": None,
        "summary": None,
        "previous": None,
    }


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


def test_report_rounds_counts_like_the_saved_summary(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """2.5 를 round() 로 세면 2 가 된다. 리포트와 Take 목록 · 비교가 같은 3 을 보여야 한다."""
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    take.analysis_stage = "SCORING"
    take_result = {
        "take_id": str(take.id),
        "duration_ms": 60_000,
        "areas": {
            "SPEED": {"measured_ratio": 1.0, "take": {"cpm": 300}},
            "FILLER": {"measured_ratio": 1.0, "take": {"filler_count": 2.5}},
        },
    }
    _add_result(db_session, take, "TAKE_RESULT", take_result)

    measures = _report(client, take.id, auth_headers).json()["measures"]
    summary = summary_measures(take_result)

    assert measures["filler_count"] == summary.filler_count == 3
    assert measures["cpm"] == summary.average_cpm == 300


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


def _add_coverage(db: Session, take: Take, payload: dict, *, revision: int = 0) -> None:
    db.add(
        TakeAIResults(
            take_id=take.id, kind="SCRIPT_COVERAGE", payload=payload, meta=META, revision=revision
        )
    )
    db.flush()


def _content_status(client: TestClient, take: Take, headers: dict[str, str]) -> dict:
    return _report(client, take.id, headers).json()["content_status"]


@pytest.mark.parametrize(
    "status,stage,coverage,failure_code,expected",
    [
        ("FAILED", "FINALIZING", None, None, ("NOT_MEASURED", "TAKE_FAILED")),
        ("FAILED", "FINALIZING", "detail", "AI_ERROR", ("NOT_MEASURED", "TAKE_FAILED")),
        ("ANALYZING", "AWAITING_CONFIRMATION", "detail", None, ("MEASURED", None)),
        ("COMPLETED", "DONE", "detail", None, ("MEASURED", None)),
        ("COMPLETED", "DONE", "legacy", None, ("NOT_MEASURED", "LEGACY_RESULT")),
        ("ANALYZING", "FINALIZING", None, None, ("ANALYZING", None)),
        # 전체 재시도 중이면 앞 시도의 실패 이유가 있어도 채점 중이다
        ("ANALYZING", "SCORING", None, "AI_UNAVAILABLE", ("ANALYZING", None)),
        ("ANALYZING", "REVIEWING", None, "AI_TIMEOUT", ("FAILED", "AI_TIMEOUT")),
        ("COMPLETED", "DONE", None, "AI_REJECTED", ("FAILED", "AI_REJECTED")),
        ("ANALYZING", "REVIEWING", None, None, ("NOT_MEASURED", "NO_RESULT")),
        ("COMPLETED", "DONE", None, None, ("NOT_MEASURED", "NO_RESULT")),
    ],
)
def test_content_status_follows_the_first_matching_row(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
    stage: str,
    coverage: str | None,
    failure_code: str | None,
    expected: tuple[str, str | None],
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status=status)
    take.analysis_stage = stage
    take.coverage_failure_code = failure_code
    if coverage is not None:
        payload = scored(take.id)
        if coverage == "legacy":
            del payload["detail"]
        _add_coverage(db_session, take, payload)

    body = _content_status(client, take, auth_headers)

    assert (body["state"], body["reason_code"]) == expected
    if body["state"] != "MEASURED":
        assert [body[key] for key in ("summary", "revision", "criteria_version", "previous")] == [
            None
        ] * 4


def test_measured_content_status_carries_the_saved_take_detail_as_is(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    # 병합 rescore 가 끝내 실패해 리뷰를 보류해도 확정본은 그대로 보인다
    take = _make_take(db_session, pitch_id, versions, 1, status="ANALYZING")
    take.analysis_stage = "SCORING"
    take.analysis_hold_reason = "COVERAGE_RESCORE_FAILED"
    _add_coverage(db_session, take, scored(take.id), revision=2)
    # 병합 후보는 읽지 않는다
    _add_result(db_session, take, "SCRIPT_COVERAGE_PENDING", {"detail": {"version": 99}})

    body = _content_status(client, take, auth_headers)

    assert body == {
        "state": "MEASURED",
        "reason_code": None,
        "revision": 2,
        "criteria_version": CRITERIA_VERSION,
        "summary": TAKE_DETAIL,
        "previous": None,
    }


def _completed(db: Session, pitch_id: uuid.UUID, versions: Versions, number: int, **values):
    take = _make_take(db, pitch_id, versions, number, status=values.pop("status", "COMPLETED"))
    _add_result(db, take, "TAKE_RESULT", {"take_id": str(take.id), "duration_ms": 60_000})
    return take


def test_previous_is_the_take_just_before_like_the_review(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    first = _completed(db_session, pitch_id, versions, 1)
    _add_coverage(db_session, first, all_failed(first.id))
    second = _completed(db_session, pitch_id, versions, 2)
    _add_coverage(db_session, second, scored(second.id))
    # 리뷰처럼 끝내 실패한 Take 와 뒤 번호 Take 는 앞 Take 가 아니다
    _completed(db_session, pitch_id, versions, 3, status="FAILED")
    current = _completed(db_session, pitch_id, versions, 4, status="ANALYZING")
    current.analysis_stage = "AWAITING_CONFIRMATION"
    _add_coverage(db_session, current, scored(current.id))
    _completed(db_session, pitch_id, versions, 5)

    previous = _content_status(client, current, auth_headers)["previous"]

    assert previous == {
        "take_id": str(second.id),
        "take_number": 2,
        "criteria_version": CRITERIA_VERSION,
        "delivery": TAKE_DETAIL["delivery"],
        "contradiction": TAKE_DETAIL["contradiction"],
        "scope": TAKE_DETAIL["scope"],
    }


@pytest.mark.parametrize("before", ["legacy", "no-coverage", "none"])
def test_previous_is_null_unless_the_take_just_before_has_a_detail(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    before: str,
) -> None:
    number = 1
    if before != "none":
        # 더 앞 Take 에 상세가 있어도 건너뛰지 않는다
        oldest = _completed(db_session, pitch_id, versions, 1)
        _add_coverage(db_session, oldest, scored(oldest.id))
        previous = _completed(db_session, pitch_id, versions, 2)
        if before == "legacy":
            payload = scored(previous.id)
            del payload["detail"]
            _add_coverage(db_session, previous, payload)
        number = 3
    current = _completed(db_session, pitch_id, versions, number)
    _add_coverage(db_session, current, scored(current.id))

    assert _content_status(client, current, auth_headers)["previous"] is None


@pytest.mark.parametrize(
    "status,stage,coverage",
    [
        ("ANALYZING", "SCORING", None),
        ("COMPLETED", "DONE", "legacy"),
        ("FAILED", "FINALIZING", None),
    ],
)
def test_previous_is_null_unless_this_take_is_measured(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
    stage: str,
    coverage: str | None,
) -> None:
    """비교할 이번 값이 없으면 앞 Take 에 상세가 있어도 싣지 않는다. 분석 중에는 FE 가 리포트를
    2초마다 다시 읽는다."""
    before = _completed(db_session, pitch_id, versions, 1)
    _add_coverage(db_session, before, scored(before.id))
    current = _make_take(db_session, pitch_id, versions, 2, status=status)
    current.analysis_stage = stage
    if coverage == "legacy":
        payload = scored(current.id)
        del payload["detail"]
        _add_coverage(db_session, current, payload)

    assert _content_status(client, current, auth_headers)["previous"] is None
