import json
import uuid

import pytest
from sqlalchemy import func, select

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.service_feedback.entity import (
    FirstReleaseFeedback,
    UserSuggestion,
)
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_first_release

SUGGESTIONS = "/api/suggestions"


def test_requires_authentication(client):
    body = {"request_id": str(uuid.uuid4()), "content": "의견"}
    assert client.post(SUGGESTIONS, json=body).status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/service-feedback/me"),
        ("post", "/api/service-feedback/impressions"),
        ("post", "/api/service-feedback"),
    ],
)
def test_first_release_popup_api_is_gone(client, auth, method, path):
    assert getattr(client, method)(path, headers=auth).status_code in (404, 405)


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
    assert db_session.scalar(select(func.count()).select_from(UserSuggestion)) == 2


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
    "body",
    [
        {"request_id": str(uuid.uuid4()), "content": "의견", "source": "RATING_POPUP"},
        {"request_id": "not-a-uuid", "content": "의견"},
    ],
)
def test_rejects_forged_or_invalid_fields(client, auth, body):
    assert client.post(SUGGESTIONS, headers=auth, json=body).status_code == 422


def test_withdraw_retains_first_release_feedback_and_opinions(client, auth, user_id, db_session):
    make_first_release(db_session, user_id, 5, "팝업 의견")
    client.post(
        SUGGESTIONS,
        headers=auth,
        json={"request_id": str(uuid.uuid4()), "content": "마이페이지 의견"},
    )
    assert client.delete("/api/users/me", headers=auth).status_code == 204
    db_session.expire_all()
    feedback = db_session.scalar(select(FirstReleaseFeedback))
    opinions = db_session.scalars(select(UserSuggestion)).all()
    assert feedback.user_id is None
    assert feedback.rating == 5
    assert all(row.user_id is None for row in opinions)
    popup = next(row for row in opinions if row.source == "RATING_POPUP")
    assert popup.service_feedback_id == feedback.id
    assert {row.content for row in opinions} == {"팝업 의견", "마이페이지 의견"}
