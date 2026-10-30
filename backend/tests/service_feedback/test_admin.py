import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.service_feedback.dto import TakeSurveyCreate
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_report

ADMIN = ("admin", "console-test-password")
PATHS = [
    "/admin/api/take-surveys",
    "/admin/api/take-surveys/summary",
    "/admin/api/suggestions",
]
T0 = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", ADMIN[0])
    monkeypatch.setattr(settings, "admin_password", ADMIN[1])


@pytest.mark.parametrize("path", PATHS)
def test_admin_disabled_is_404(client, path):
    assert client.get(path, auth=ADMIN).status_code == 404


@pytest.mark.parametrize("path", PATHS)
def test_admin_auth_required(client, console, path):
    assert client.get(path).status_code == 401
    assert client.get(path, auth=("admin", "wrong")).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/admin/api/service-feedbacks",
        "/admin/api/service-feedbacks/summary",
        "/admin/api/service-feedbacks/by-user",
    ],
)
def test_first_release_rating_views_are_gone(client, console, path):
    assert client.get(path, auth=ADMIN).status_code == 404


def _user(db):
    user = User(email=f"{uuid.uuid4()}@example.com", name="사용자")
    db.add(user)
    db.flush()
    return user.id


def _suggest(db, user_id, content, *, minute):
    row, _ = service.submit_suggestion(db, user_id, request_id=uuid.uuid4(), content=content)
    row.created_at = T0 + timedelta(minutes=minute)
    db.commit()
    return row


def test_admin_lists_suggestions_by_page_and_user(client, console, db_session):
    mine, other = _user(db_session), _user(db_session)
    for number in range(3):
        _suggest(db_session, mine, f"의견 {number}", minute=number)
    _suggest(db_session, other, "<script>다른 사용자</script>", minute=10)

    first = client.get("/admin/api/suggestions?limit=2", auth=ADMIN).json()
    assert [row["content"] for row in first["items"]] == ["<script>다른 사용자</script>", "의견 2"]
    assert first["next_offset"] == 2
    filtered = client.get(f"/admin/api/suggestions?user_id={mine}&offset=1", auth=ADMIN).json()
    assert [row["content"] for row in filtered["items"]] == ["의견 1", "의견 0"]
    assert filtered["next_offset"] is None
    assert all(row["source"] == "MY_PAGE" for row in first["items"])
    assert "email" not in str(first)


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "offset=-1", "source=RATING_POPUP", "user_id=not-a-uuid"],
)
def test_admin_validates_suggestion_query(client, console, query):
    assert client.get(f"/admin/api/suggestions?{query}", auth=ADMIN).status_code == 422


def test_admin_can_read_suggestions_after_withdrawal(client, console, db_session):
    gone = _user(db_session)
    _suggest(db_session, gone, "남는 의견", minute=0)
    user_service.delete(db_session, user_service.get(db_session, gone))
    rows = client.get("/admin/api/suggestions", auth=ADMIN).json()["items"]
    assert [(row["user_id"], row["content"]) for row in rows] == [(None, "남는 의견")]


def _survey(db, user_id, accuracy, usefulness, comment=None, *, version=1, minute):
    take = make_report(db, user_id)
    body = TakeSurveyCreate(
        accuracy_rating=accuracy,
        usefulness_rating=usefulness,
        comment=comment,
        question_version=version,
    )
    survey, _ = service.submit_survey(db, user_id, take.id, body)
    survey.created_at = T0 + timedelta(minutes=minute)
    db.commit()
    return take, survey


def test_admin_lists_take_surveys_and_keeps_deleted_links(client, console, db_session):
    kept, gone = _user(db_session), _user(db_session)
    take, _ = _survey(db_session, kept, 4, 3, "<b>속도</b>가 이상해요", minute=0)
    removed, _ = _survey(db_session, kept, 2, 2, minute=1)
    _, withdrawn = _survey(db_session, gone, 5, 5, minute=2)
    db_session.delete(removed)
    # 탈퇴한 뒤의 모양만 만든다. 실제 탈퇴는 test_concurrency 가 커밋된 설문으로 본다
    withdrawn.user_id = None
    db_session.commit()

    first = client.get("/admin/api/take-surveys?limit=2", auth=ADMIN).json()
    assert first["next_offset"] == 2
    withdrawn, deleted = first["items"]
    assert (withdrawn["user_id"], withdrawn["accuracy_rating"]) == (None, 5)
    assert (deleted["take_id"], deleted["pitch_title"], deleted["take_number"]) == (
        None,
        None,
        None,
    )
    last = client.get("/admin/api/take-surveys?limit=2&offset=2", auth=ADMIN).json()
    assert last["next_offset"] is None
    assert last["items"][0] | {"id": None, "created_at": None} == {
        "id": None,
        "user_id": str(kept),
        "take_id": str(take.id),
        "pitch_title": "발표",
        "take_number": 1,
        "accuracy_rating": 4,
        "usefulness_rating": 3,
        "comment": "<b>속도</b>가 이상해요",
        "question_version": 1,
        "created_at": None,
    }
    assert "email" not in str(first) + str(last)


def test_admin_take_survey_summary_by_question_version(client, console, db_session):
    path = "/admin/api/take-surveys/summary"
    assert client.get(path, auth=ADMIN).json() == []
    user = _user(db_session)
    _survey(db_session, user, 4, 2, minute=0)
    _survey(db_session, user, 3, 5, minute=1)
    _survey(db_session, user, 1, 1, version=2, minute=2)
    assert client.get(path, auth=ADMIN).json() == [
        {"question_version": 2, "survey_count": 1, "avg_accuracy": 1.0, "avg_usefulness": 1.0},
        {"question_version": 1, "survey_count": 2, "avg_accuracy": 3.5, "avg_usefulness": 3.5},
    ]
