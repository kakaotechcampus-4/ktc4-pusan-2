import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.service_feedback.entity import UserSuggestion
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory
from tests.service_feedback.conftest import make_first_release

SUGGESTIONS = "/api/suggestions"
CATEGORIES = ["POSITIVE", "IMPROVEMENT", "PROBLEM", "QUESTION", "OTHER"]
ADMIN = ("admin", "category-test-password")


def payload(category):
    return {"request_id": str(uuid.uuid4()), "content": "의견", "category": category}


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_is_accepted_and_saved(client, auth, db_session, category):
    response = client.post(SUGGESTIONS, headers=auth, json=payload(category))
    assert response.status_code == 201
    opinion = db_session.scalar(select(UserSuggestion))
    assert opinion.category == category
    assert opinion.source == "MY_PAGE"
    assert response.json()["category"] == category


@pytest.mark.parametrize("category", ["UNKNOWN", "positive", "", None, 1])
def test_invalid_category_is_422(client, auth, category):
    assert client.post(SUGGESTIONS, headers=auth, json=payload(category)).status_code == 422


def test_omitted_category_defaults_to_other(client, auth, db_session):
    body = payload("OTHER")
    del body["category"]
    assert client.post(SUGGESTIONS, headers=auth, json=body).status_code == 201
    assert db_session.scalar(select(UserSuggestion.category)) == "OTHER"


def test_retry_must_keep_category(client, auth):
    body = payload("POSITIVE")
    first = client.post(SUGGESTIONS, headers=auth, json=body)
    assert first.status_code == 201
    replay = client.post(SUGGESTIONS, headers=auth, json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    conflict = client.post(SUGGESTIONS, headers=auth, json={**body, "category": "PROBLEM"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "SUGGESTION_REQUEST_CONFLICT"


def test_category_remains_after_withdrawal(client, auth, db_session):
    assert client.post(SUGGESTIONS, headers=auth, json=payload("QUESTION")).status_code == 201
    assert client.delete("/api/users/me", headers=auth).status_code == 204
    db_session.expire_all()
    opinion = db_session.scalar(select(UserSuggestion))
    assert opinion.user_id is None
    assert opinion.category == "QUESTION"


def test_db_rejects_unknown_category(db_session, user_id):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            UserSuggestion(
                user_id=user_id,
                source="MY_PAGE",
                content="의견",
                request_id=uuid.uuid4(),
                category="UNKNOWN",
            )
        )
        db_session.flush()


def test_admin_filters_category_and_source_together(client, auth, user_id, db_session, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", ADMIN[0])
    monkeypatch.setattr(settings, "admin_password", ADMIN[1])
    for category in CATEGORIES:
        assert client.post(SUGGESTIONS, headers=auth, json=payload(category)).status_code == 201
    make_first_release(db_session, user_id, 4, "의견", category="PROBLEM")
    all_problems = client.get("/admin/api/suggestions?category=PROBLEM", auth=ADMIN).json()
    assert len(all_problems["items"]) == 2
    assert all(row["category"] == "PROBLEM" for row in all_problems["items"])
    popup = client.get(
        "/admin/api/suggestions?category=PROBLEM&source=RATING_POPUP", auth=ADMIN
    ).json()
    assert len(popup["items"]) == 1
    ratings = client.get("/admin/api/service-feedbacks", auth=ADMIN).json()
    assert ratings["items"][0]["category"] == "PROBLEM"
    assert client.get("/admin/api/suggestions?category=UNKNOWN", auth=ADMIN).status_code == 422


def test_openapi_describes_category_enum(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
    assert schemas["SuggestionCategory"]["enum"] == CATEGORIES
    assert schemas["SuggestionCreate"]["properties"]["category"]["default"] == "OTHER"
    assert [category.value for category in SuggestionCategory] == CATEGORIES
