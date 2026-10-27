import json
import uuid

import pytest
from sqlalchemy import func, select

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.service_feedback import repository, service
from pitch_coach_backend.module.service_feedback.entity import (
    ServiceFeedback,
    ServiceFeedbackImpression,
    UserSuggestion,
)
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_report

FEEDBACK = "/api/service-feedback"
IMPRESSIONS = f"{FEEDBACK}/impressions"
STATE = f"{FEEDBACK}/me"
SUGGESTIONS = "/api/suggestions"


def approve(client, auth, request_id=None):
    return client.post(
        IMPRESSIONS, headers=auth, json={"display_request_id": str(request_id or uuid.uuid4())}
    )


@pytest.mark.parametrize(
    "path,method,body",
    [
        (STATE, "get", None),
        (IMPRESSIONS, "post", {"display_request_id": str(uuid.uuid4())}),
        (FEEDBACK, "post", {"rating": 5}),
        (SUGGESTIONS, "post", {"request_id": str(uuid.uuid4()), "content": "의견"}),
    ],
)
def test_requires_authentication(client, path, method, body):
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401


def test_no_report_does_not_consume_a_display(client, auth, db_session):
    response = approve(client, auth)
    assert response.status_code == 200
    assert response.json() == {
        "should_open": False,
        "submitted": False,
        "display_count": 0,
        "display_limit": 3,
        "reason": "NO_REPORT",
    }
    assert db_session.scalar(select(func.count()).select_from(ServiceFeedbackImpression)) == 0
    assert (
        client.post(FEEDBACK, headers=auth, json={"rating": 5}).json()["code"]
        == "SERVICE_FEEDBACK_NOT_ELIGIBLE"
    )


@pytest.mark.parametrize(
    "status,result,eligible",
    [
        ("READY", True, False),
        ("RUNNING", True, False),
        ("ANALYZING", True, False),
        ("FAILED", True, False),
        ("COMPLETED", False, False),
        ("COMPLETED", True, True),
    ],
)
def test_report_requires_completion_and_result(
    client, auth, db_session, user_id, status, result, eligible
):
    make_report(db_session, user_id, status=status, result=result)
    assert client.get(STATE, headers=auth).json()["has_report"] is eligible
    assert approve(client, auth).json()["should_open"] is eligible


@pytest.mark.parametrize("review_status", ["FAILED", "SKIPPED", "DONE"])
def test_ai_review_status_does_not_hide_measurement_report(
    client, auth, db_session, user_id, review_status
):
    make_report(db_session, user_id, review_status=review_status)
    assert approve(client, auth).json()["should_open"]


def test_other_users_report_is_not_eligible(client, auth, db_session):
    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(user)
    db_session.flush()
    make_report(db_session, user.id)
    assert not approve(client, auth).json()["should_open"]


def test_three_displays_and_lost_third_response_retry(client, auth, report, db_session):
    ids = [uuid.uuid4() for _ in range(3)]
    for count, request_id in enumerate(ids, 1):
        response = approve(client, auth, request_id)
        assert response.json()["should_open"]
        assert response.json()["display_count"] == count
    assert not client.get(STATE, headers=auth).json()["should_prompt"]
    assert approve(client, auth, ids[-1]).json()["should_open"]
    assert approve(client, auth).json()["reason"] == "DISPLAY_LIMIT_REACHED"
    assert db_session.scalar(select(func.count()).select_from(ServiceFeedbackImpression)) == 3
    # 한도는 표시 승인에만 적용한다. 세 번째 팝업의 평점은 계속 제출할 수 있다.
    assert client.post(FEEDBACK, headers=auth, json={"rating": 5}).status_code == 201
    assert approve(client, auth, ids[-1]).json()["reason"] == "ALREADY_SUBMITTED"


def test_deleted_report_stops_display_without_resetting_count(client, auth, report, db_session):
    request_id = uuid.uuid4()
    assert approve(client, auth, request_id).json()["should_open"]
    db_session.delete(report)
    db_session.commit()
    response = approve(client, auth, request_id)
    assert response.json()["reason"] == "NO_REPORT"
    assert response.json()["display_count"] == 1


def test_rating_and_optional_opinion_saved_atomically_and_retried(client, auth, report, db_session):
    body = {"rating": 4, "suggestion": "  의견입니다  "}
    first = client.post(FEEDBACK, headers=auth, json=body)
    assert first.status_code == 201
    assert "suggestion" not in first.json()
    feedback = db_session.scalar(select(ServiceFeedback))
    opinion = db_session.scalar(select(UserSuggestion))
    assert (opinion.content, opinion.source, opinion.service_feedback_id) == (
        "의견입니다",
        "RATING_POPUP",
        feedback.id,
    )
    client.post(
        SUGGESTIONS, headers=auth, json={"request_id": str(uuid.uuid4()), "content": "다른 의견"}
    )
    db_session.delete(report)
    db_session.commit()
    replay = client.post(FEEDBACK, headers=auth, json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert (
        client.post(FEEDBACK, headers=auth, json={"rating": 3}).json()["code"]
        == "SERVICE_FEEDBACK_ALREADY_SUBMITTED"
    )
    assert db_session.scalar(select(func.count()).select_from(ServiceFeedback)) == 1
    assert not client.get(STATE, headers=auth).json()["should_prompt"]


@pytest.mark.parametrize("suggestion", [None, "", "   "])
def test_optional_opinion_creates_no_row(client, auth, report, db_session, suggestion):
    assert (
        client.post(
            FEEDBACK, headers=auth, json={"rating": 1, "suggestion": suggestion}
        ).status_code
        == 201
    )
    assert client.post(FEEDBACK, headers=auth, json={"rating": 1}).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(UserSuggestion)) == 0


@pytest.mark.parametrize("rating", [0, 6, True, False, "3", 3.5, 3.0, None])
def test_invalid_rating_is_422(client, auth, rating):
    assert client.post(FEEDBACK, headers=auth, json={"rating": rating}).status_code == 422


@pytest.mark.parametrize("content", ["", "  ", None, "가" * 2001, "bad\x00text", "\ud800"])
def test_mypage_invalid_content_is_422(client, auth, content):
    response = client.post(
        SUGGESTIONS,
        headers={**auth, "Content-Type": "application/json"},
        content=json.dumps({"request_id": str(uuid.uuid4()), "content": content}),
    )
    assert response.status_code == 422


def test_mypage_accepts_2000_characters_and_retries_independently(client, auth, db_session):
    request_id = str(uuid.uuid4())
    body = {"request_id": request_id, "content": "가" * 2000}
    first = client.post(SUGGESTIONS, headers=auth, json=body)
    assert first.status_code == 201
    assert client.post(SUGGESTIONS, headers=auth, json=body).json() == first.json()
    assert client.post(SUGGESTIONS, headers=auth, json=body).status_code == 200
    conflict = client.post(SUGGESTIONS, headers=auth, json={**body, "content": "바꾼 내용"})
    assert (conflict.status_code, conflict.json()["code"]) == (409, "SUGGESTION_REQUEST_CONFLICT")
    assert (
        client.post(
            SUGGESTIONS, headers=auth, json={**body, "request_id": str(uuid.uuid4())}
        ).status_code
        == 201
    )
    assert not client.get(STATE, headers=auth).json()["submitted"]
    assert client.get(STATE, headers=auth).json()["display_count"] == 0
    assert db_session.scalar(select(func.count()).select_from(UserSuggestion)) == 2


def test_mypage_is_allowed_after_rating_and_three_displays(client, auth, report, db_session):
    for _ in range(3):
        approve(client, auth)
    client.post(FEEDBACK, headers=auth, json={"rating": 5})
    assert (
        client.post(
            SUGGESTIONS,
            headers=auth,
            json={"request_id": str(uuid.uuid4()), "content": "후속 의견"},
        ).status_code
        == 201
    )
    assert client.get(STATE, headers=auth).json()["display_count"] == 3


def test_request_ids_are_scoped_to_user(client, auth, db_session):
    body = {"request_id": str(uuid.uuid4()), "content": "의견"}
    first = client.post(SUGGESTIONS, headers=auth, json=body)
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    second = client.post(
        SUGGESTIONS, headers={"Authorization": f"Bearer {create_access_token(other.id)}"}, json=body
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.parametrize(
    "path,body",
    [
        (FEEDBACK, {"rating": 5, "user_id": str(uuid.uuid4())}),
        (
            SUGGESTIONS,
            {"request_id": str(uuid.uuid4()), "content": "의견", "source": "RATING_POPUP"},
        ),
        (IMPRESSIONS, {"display_request_id": "not-a-uuid"}),
    ],
)
def test_rejects_forged_or_invalid_fields(client, auth, path, body):
    assert client.post(path, headers=auth, json=body).status_code == 422


def test_opinion_failure_rolls_back_rating(db_session, user_id, report, monkeypatch):
    db_session.commit()

    def fail(*args):
        raise RuntimeError("저장 실패")

    monkeypatch.setattr(repository, "add_popup_suggestion", fail)
    with pytest.raises(RuntimeError):
        service.submit_feedback(db_session, user_id, rating=4, suggestion="의견")
    assert db_session.scalar(select(func.count()).select_from(ServiceFeedback)) == 0


def test_withdraw_cascades_and_old_token_is_rejected(client, auth, report, db_session):
    approve(client, auth)
    client.post(FEEDBACK, headers=auth, json={"rating": 5, "suggestion": "팝업 의견"})
    client.post(
        SUGGESTIONS,
        headers=auth,
        json={"request_id": str(uuid.uuid4()), "content": "마이페이지 의견"},
    )
    assert client.delete("/api/users/me", headers=auth).status_code == 204
    for entity in (ServiceFeedback, UserSuggestion, ServiceFeedbackImpression):
        assert db_session.scalar(select(func.count()).select_from(entity)) == 0
    assert client.get(STATE, headers=auth).status_code == 401


def test_state_is_read_only_and_not_cacheable(client, auth, report, db_session):
    for _ in range(2):
        response = client.get(STATE, headers=auth)
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["should_prompt"]
    assert db_session.scalar(select(func.count()).select_from(ServiceFeedbackImpression)) == 0
