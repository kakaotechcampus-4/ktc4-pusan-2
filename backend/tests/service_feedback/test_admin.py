import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.service_feedback import service
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User
from tests.service_feedback.conftest import make_first_release

ADMIN = ("admin", "console-test-password")
PATHS = [
    "/admin/api/service-feedbacks",
    "/admin/api/suggestions",
    "/admin/api/service-feedbacks/by-user",
    "/admin/api/service-feedbacks/summary",
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


def test_admin_lists_popup_and_mypage_independently(client, console, db_session, user_id):
    feedback = make_first_release(db_session, user_id, 4, "<script>의견</script>")
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


def test_admin_can_read_retained_feedback_after_withdrawal(client, console, db_session, user_id):
    feedback = make_first_release(db_session, user_id, 5, "팝업 의견")
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


def _user(db):
    user = User(email=f"{uuid.uuid4()}@example.com", name="사용자")
    db.add(user)
    db.flush()
    return user.id


def _rate(db, user_id, rating, suggestion=None, *, minute):
    return make_first_release(
        db, user_id, rating, suggestion, created_at=T0 + timedelta(minutes=minute)
    )


def _suggest(db, user_id, content, *, minute):
    row, _ = service.submit_suggestion(db, user_id, request_id=uuid.uuid4(), content=content)
    row.created_at = T0 + timedelta(minutes=minute)
    db.commit()
    return row


def _by_user(client, limit=50):
    rows, offset = [], 0
    while offset is not None:
        page = client.get(
            f"/admin/api/service-feedbacks/by-user?limit={limit}&offset={offset}", auth=ADMIN
        ).json()
        rows += page["items"]
        offset = page["next_offset"]
    return rows


def test_admin_by_user_groups_rating_and_all_suggestions(client, console, db_session):
    both, rated, opinion_only = (
        _user(db_session),
        _user(db_session),
        _user(db_session),
    )
    _rate(db_session, both, 4, "<b>팝업</b>\n두 줄", minute=0)
    mine = [_suggest(db_session, both, f"마이페이지 {n}", minute=10 + n) for n in range(4)]
    _rate(db_session, rated, 5, minute=20)
    _suggest(db_session, opinion_only, "의견만", minute=5)

    for limit in (1, 2, 50):
        rows = _by_user(client, limit)
        assert [row["user_id"] for row in rows] == [str(rated), str(both), str(opinion_only)]
    first = rows[1]
    assert first["rating"] == 4
    assert first["retained_id"] is None
    assert first["suggestion_count"] == 5
    assert [row["content"] for row in first["suggestions"]] == [
        "마이페이지 3",
        "마이페이지 2",
        "마이페이지 1",
    ]
    rest = client.get(
        f"/admin/api/suggestions?user_id={both}&offset={len(first['suggestions'])}", auth=ADMIN
    ).json()
    assert [row["content"] for row in rest["items"]] == ["마이페이지 0", "<b>팝업</b>\n두 줄"]
    shown = {row["id"] for row in first["suggestions"] + rest["items"]}
    assert shown >= {str(row.id) for row in mine}
    assert len(shown) == 5
    assert (rows[0]["rating"], rows[0]["suggestion_count"], rows[0]["suggestions"]) == (5, 0, [])
    assert (rows[2]["rating"], rows[2]["rated_at"], rows[2]["suggestion_count"]) == (None, None, 1)
    assert "email" not in str(rows)


def test_admin_by_user_keeps_withdrawn_authors_apart(client, console, db_session):
    gone = [_user(db_session), _user(db_session)]
    feedbacks, opinions = [], []
    for n, user_id in enumerate(gone):
        feedbacks.append(_rate(db_session, user_id, n + 1, f"팝업 {n}", minute=n))
        opinions.append(_suggest(db_session, user_id, f"추가 {n}", minute=10 + n))
    active = _user(db_session)
    _suggest(db_session, active, "남은 사용자", minute=30)
    for user_id in gone:
        user_service.delete(db_session, user_service.get(db_session, user_id))

    rows = _by_user(client, limit=2)
    assert rows[0]["user_id"] == str(active)
    retained = rows[1:]
    assert all(row["user_id"] is None for row in retained)
    assert [row["retained_id"] for row in retained] == [
        str(opinions[1].id),
        str(opinions[0].id),
        str(feedbacks[1].id),
        str(feedbacks[0].id),
    ]
    assert [row["rating"] for row in retained] == [None, None, 2, 1]
    assert [[s["content"] for s in row["suggestions"]] for row in retained] == [
        ["추가 1"],
        ["추가 0"],
        ["팝업 1"],
        ["팝업 0"],
    ]


def test_admin_by_user_empty(client, console):
    response = client.get("/admin/api/service-feedbacks/by-user", auth=ADMIN)
    assert response.json() == {"items": [], "next_offset": None}


@pytest.mark.parametrize(
    "path",
    [
        "/admin/api/service-feedbacks/by-user?limit=0",
        "/admin/api/service-feedbacks/by-user?offset=-1",
        "/admin/api/suggestions?user_id=not-a-uuid",
    ],
)
def test_admin_by_user_validates_query(client, console, path):
    assert client.get(path, auth=ADMIN).status_code == 422


def test_admin_rating_summary_skips_unrated_and_keeps_withdrawn(client, console, db_session):
    path = "/admin/api/service-feedbacks/summary"
    assert client.get(path, auth=ADMIN).json() == {"rated_count": 0, "avg_rating": None}
    kept, gone = _user(db_session), _user(db_session)
    _rate(db_session, kept, 4, minute=0)
    _rate(db_session, gone, 3, "팝업", minute=1)
    _suggest(db_session, _user(db_session), "의견만", minute=2)
    user_service.delete(db_session, user_service.get(db_session, gone))
    assert client.get(path, auth=ADMIN).json() == {"rated_count": 2, "avg_rating": 3.5}
