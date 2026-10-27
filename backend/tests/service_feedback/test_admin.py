import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.user import service as user_service

ADMIN = ("admin", "console-test-password")


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", ADMIN[0])
    monkeypatch.setattr(settings, "admin_password", ADMIN[1])


@pytest.mark.parametrize("path", ["/admin/api/service-feedbacks", "/admin/api/suggestions"])
def test_admin_disabled_is_404(client, path):
    assert client.get(path, auth=ADMIN).status_code == 404


@pytest.mark.parametrize("path", ["/admin/api/service-feedbacks", "/admin/api/suggestions"])
def test_admin_auth_required(client, console, path):
    assert client.get(path).status_code == 401
    assert client.get(path, auth=("admin", "wrong")).status_code == 401


def test_admin_lists_popup_and_mypage_independently(client, console, db_session, user_id, report):
    feedback, _ = service.submit_feedback(
        db_session, user_id, rating=4, suggestion="<script>의견</script>"
    )
    for number in range(3):
        row, _ = service.submit_suggestion(
            db_session, user_id, request_id=uuid.uuid4(), content=f"의견 {number}"
        )
        row.created_at = datetime(2026, 10, 27, tzinfo=UTC) + timedelta(minutes=number)
        db_session.commit()
    ratings = client.get("/admin/api/service-feedbacks?limit=1", auth=ADMIN).json()
    assert ratings["items"][0]["id"] == str(feedback.id)
    assert ratings["items"][0]["suggestion"] == "<script>의견</script>"
    assert ratings["next_offset"] is None
    first = client.get("/admin/api/suggestions?source=MY_PAGE&limit=2", auth=ADMIN).json()
    assert [row["content"] for row in first["items"]] == ["의견 2", "의견 1"]
    assert first["next_offset"] == 2
    last = client.get("/admin/api/suggestions?source=MY_PAGE&limit=2&offset=2", auth=ADMIN).json()
    assert [row["content"] for row in last["items"]] == ["의견 0"]
    assert last["next_offset"] is None
    popup = client.get("/admin/api/suggestions?source=RATING_POPUP", auth=ADMIN).json()
    assert len(popup["items"]) == 1
    assert popup["items"][0]["service_feedback_id"] == str(feedback.id)
    assert "email" not in str(popup)


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "source=UNKNOWN"])
def test_admin_validates_pagination_and_source(client, console, query):
    assert client.get(f"/admin/api/suggestions?{query}", auth=ADMIN).status_code == 422


def test_admin_can_read_retained_feedback_after_withdrawal(
    client, console, db_session, user_id, report
):
    feedback, _ = service.submit_feedback(db_session, user_id, rating=5, suggestion="팝업 의견")
    service.submit_suggestion(db_session, user_id, request_id=uuid.uuid4(), content="추가 의견")
    user_service.delete(db_session, user_service.get(db_session, user_id))
    ratings = client.get("/admin/api/service-feedbacks", auth=ADMIN)
    opinions = client.get("/admin/api/suggestions", auth=ADMIN)
    assert ratings.status_code == opinions.status_code == 200
    assert ratings.json()["items"][0]["id"] == str(feedback.id)
    assert ratings.json()["items"][0]["user_id"] is None
    assert ratings.json()["items"][0]["suggestion"] == "팝업 의견"
    assert len(opinions.json()["items"]) == 2
    assert all(row["user_id"] is None for row in opinions.json()["items"])
