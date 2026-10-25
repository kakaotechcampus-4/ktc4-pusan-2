"""관리자 콘솔 (/admin) — 로그인, 화면 파일, 집계 API."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take, TakeTranscriptSegment
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.stream.runtime import ABANDON_AFTER_SEC

ADMIN = ("admin", "console-password")


@pytest.fixture
def admin_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_username", ADMIN[0])
    monkeypatch.setattr(settings, "admin_password", ADMIN[1])


def _user(db: Session, name: str) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", name=name)
    db.add(user)
    db.flush()
    return user


def _pitch(db: Session, user: User, title: str = "발표") -> Pitch:
    pitch = Pitch(user_id=user.id, title=title, time_limit_sec=300)
    db.add(pitch)
    db.flush()
    db.add_all([
        PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
        ScriptVersion(pitch_id=pitch.id, version=1, content="대본"),
    ])
    db.flush()
    return pitch


def _take(db: Session, pitch: Pitch, number: int, **fields) -> Take:
    presentation = db.query(PresentationVersion).filter_by(pitch_id=pitch.id).one()
    script = db.query(ScriptVersion).filter_by(pitch_id=pitch.id).one()
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode=fields.pop("mode", "COACHING"),
        script_mode=fields.pop("script_mode", "HIGHLIGHT"),
        goal_time_sec=fields.pop("goal_time_sec", 300),
        **fields,
    )
    db.add(take)
    db.flush()
    return take


def _ended(minutes: float) -> dict:
    """`minutes` 분 동안 연습하고 사용자가 끝낸 Take 의 필드."""
    started = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    return {
        "status": "ANALYZING",
        "started_at": started,
        "ended_at": started + timedelta(minutes=minutes),
    }


# ── 로그인 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/admin/", "/admin/app.js", "/admin/api/stats"])
def test_console_is_hidden_without_admin_account(client: TestClient, path: str) -> None:
    response = client.get(path, auth=ADMIN)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_console_is_hidden_even_with_broken_login_header(client: TestClient) -> None:
    """깨진 헤더면 HTTPBasic 이 401 을 낸다. 꺼진 콘솔은 그보다 먼저 404 로 끝내야 한다."""
    response = client.get("/admin/", headers={"Authorization": "Basic !!!"})

    assert response.status_code == 404


@pytest.mark.usefixtures("admin_account")
@pytest.mark.parametrize("auth", [None, ("admin", "wrong"), ("wrong", ADMIN[1])])
def test_console_asks_for_basic_login(client: TestClient, auth) -> None:
    response = client.get("/admin/api/stats", auth=auth)

    assert response.status_code == 401
    # Bearer 가 아니라 Basic 이어야 브라우저가 로그인 창을 띄운다
    assert response.headers["www-authenticate"] == 'Basic realm="pitch-coach-admin"'


@pytest.mark.usefixtures("admin_account")
def test_wrong_login_is_logged_but_first_visit_is_not(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """브라우저는 처음에 계정 없이 와서 401 을 받고 창을 띄운다. 그건 실패로 남기지 않는다."""
    caplog.set_level(logging.WARNING, logger="pitch_coach_backend.module.admin")

    client.get("/admin/")
    client.get(
        "/admin/api/auth",
        auth=("admin", "wrong"),
        headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Uri": "/admin/logs/"},
    )

    [record] = caplog.records
    assert "username='admin'" in record.getMessage()
    assert "ip=203.0.113.7" in record.getMessage()
    assert "path=/admin/logs/" in record.getMessage()
    assert "wrong" not in record.getMessage()


@pytest.mark.usefixtures("admin_account")
@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/admin/", "text/html"),
        ("/admin/app.js", "text/javascript"),
        ("/admin/style.css", "text/css"),
    ],
)
def test_console_serves_static_files(client: TestClient, path: str, content_type: str) -> None:
    response = client.get(path, auth=ADMIN)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)


@pytest.mark.usefixtures("admin_account")
def test_auth_check_for_caddy_answers_without_body(client: TestClient) -> None:
    """Caddy 가 서버 로그를 넘기기 전에 묻는 곳 (forward_auth).

    2xx 면 통과, 아니면 이 응답을 브라우저에 그대로 돌려준다.
    """
    assert client.get("/admin/api/auth", auth=ADMIN).status_code == 204
    assert client.get("/admin/api/auth").status_code == 401


def test_console_is_not_in_public_api_docs(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]

    assert [p for p in paths if p.startswith("/admin")] == []


# ── 집계 ────────────────────────────────────────────────────────


@pytest.mark.usefixtures("admin_account")
def test_stats_counts_users_takes_and_practice_time(
    client: TestClient, db_session: Session
) -> None:
    alice = _user(db_session, "앨리스")
    _user(db_session, "밥")  # 연습하지 않은 사용자도 센다
    pitch = _pitch(db_session, alice)
    _take(db_session, pitch, 1, **_ended(2))
    _take(db_session, pitch, 2, **_ended(4), mode="EXAM")
    _take(db_session, pitch, 3, **_ended(9), end_reason="SILENCE")
    _take(db_session, pitch, 4)  # 시작 전 — 연습 시간에서 빠진다
    # 분석 결과가 온 Take 는 시작 · 끝 시각보다 duration_sec 을 쓴다
    _take(db_session, pitch, 5, **_ended(1), duration_sec=300)

    response = client.get("/admin/api/stats", auth=ADMIN)

    assert response.status_code == 200
    body = response.json()
    assert body["users"] == {"total": 2, "today": 2}
    takes = body["takes"]
    assert takes["total"] == 5
    assert takes["today"] == 5
    assert takes["by_status"] == {"ANALYZING": 4, "READY": 1}
    assert takes["by_mode"] == {"COACHING": 4, "EXAM": 1}
    assert takes["by_end_reason"] == {"USER": 3, "SILENCE": 1}
    time = body["practice_time"]
    assert time["measured_takes"] == 4
    assert time["total_sec"] == pytest.approx((2 + 4 + 9 + 5) * 60)
    assert time["avg_sec"] == pytest.approx(5 * 60)
    assert time["median_sec"] == pytest.approx(4.5 * 60)
    assert time["avg_goal_ratio"] == pytest.approx(1.0)


@pytest.mark.usefixtures("admin_account")
def test_stats_daily_has_every_day_until_today(client: TestClient, db_session: Session) -> None:
    _user(db_session, "오늘 가입")

    daily = client.get("/admin/api/stats", auth=ADMIN).json()["daily"]

    assert len(daily) == 30
    assert [d["signups"] for d in daily] == [0] * 29 + [1]
    assert all(d["takes"] == 0 for d in daily)


@pytest.mark.usefixtures("admin_account")
def test_goal_ratio_leaves_out_takes_without_goal(client: TestClient, db_session: Session) -> None:
    """입력 검증(#147) 전에 만든 Take 는 목표가 0 일 수 있다.

    0 으로 나눠 통계 전체가 실패하면 안 된다.
    """
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    _take(db_session, pitch, 1, **_ended(5))
    _take(db_session, pitch, 2, **_ended(3), goal_time_sec=0)

    response = client.get("/admin/api/stats", auth=ADMIN)

    assert response.status_code == 200
    time = response.json()["practice_time"]
    assert time["measured_takes"] == 2
    assert time["avg_goal_ratio"] == pytest.approx(1.0)


@pytest.mark.usefixtures("admin_account")
def test_stats_without_any_take_has_empty_practice_time(client: TestClient) -> None:
    time = client.get("/admin/api/stats", auth=ADMIN).json()["practice_time"]

    assert time == {
        "measured_takes": 0,
        "total_sec": 0,
        "avg_sec": None,
        "median_sec": None,
        "avg_goal_ratio": None,
    }


# ── 목록 ────────────────────────────────────────────────────────


@pytest.mark.usefixtures("admin_account")
def test_takes_filters_by_status_newest_first(client: TestClient, db_session: Session) -> None:
    pitch = _pitch(db_session, _user(db_session, "앨리스"), title="투자 발표")
    _take(db_session, pitch, 1, status="FAILED", end_reason="ABANDONED")
    _take(db_session, pitch, 2, **_ended(3))
    newest = _take(db_session, pitch, 3, status="FAILED")
    newest.created_at = datetime.now(UTC) + timedelta(seconds=1)
    db_session.flush()

    rows = client.get("/admin/api/takes", params={"status": "FAILED"}, auth=ADMIN).json()

    assert [row["take_number"] for row in rows] == [3, 1]
    assert rows[0]["pitch_title"] == "투자 발표"
    assert rows[0]["user_name"] == "앨리스"


@pytest.mark.usefixtures("admin_account")
def test_takes_rejects_unknown_status(client: TestClient) -> None:
    response = client.get("/admin/api/takes", params={"status": "DONE"}, auth=ADMIN)

    assert response.status_code == 422


def _final_segment(db: Session, take: Take, saved_at: datetime) -> None:
    db.add(
        TakeTranscriptSegment(
            take_id=take.id,
            seq=1,
            stt_session_no=1,
            start_ms=0,
            end_ms=100,
            transcript="말",
            words=[],
            confidence=0.9,
            speech_final=True,
            created_at=saved_at,
        )
    )
    db.flush()


@pytest.mark.usefixtures("admin_account")
def test_stuck_takes_are_running_without_activity_past_abandon(
    client: TestClient, db_session: Session
) -> None:
    """진행 중인데 마지막 활동 뒤 이탈 대기를 넘긴 Take.

    앱이 다시 뜰 때 떠난 Take 를 찾는 기준과 같다.

    시작 시각은 FE 가 발표를 끝낼 때에야 보내서, 진행 중인 Take 는 보통 비어 있다.
    """
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    now = datetime.now(UTC)
    long_ago = now - timedelta(seconds=ABANDON_AFTER_SEC) - timedelta(minutes=11)
    stuck = _take(db_session, pitch, 1, status="RUNNING", created_at=long_ago)
    # 오래전에 만들었지만 확정 전사가 방금 저장됐다 (장치 점검을 오래 했거나 다시 연결해 이어 간다)
    speaking = _take(db_session, pitch, 2, status="RUNNING", created_at=long_ago)
    _final_segment(db_session, speaking, now)
    _take(db_session, pitch, 3, status="RUNNING", created_at=long_ago, started_at=now)
    # 시작 전에 떠난 Take 는 정리하는 곳이 없어 쌓이기만 한다. 넣지 않는다
    _take(db_session, pitch, 4, status="READY", created_at=long_ago)
    # 끝난 Take 는 아직 전부 분석 중에 머문다. 넣지 않는다
    _take(db_session, pitch, 5, status="ANALYZING", created_at=long_ago)

    rows = client.get("/admin/api/takes/stuck", auth=ADMIN).json()

    assert [row["id"] for row in rows] == [str(stuck.id)]


@pytest.mark.usefixtures("admin_account")
def test_users_lists_counts_most_recent_practice_first(
    client: TestClient, db_session: Session
) -> None:
    _user(db_session, "연습 안 함")
    alice = _user(db_session, "앨리스")
    first, second = _pitch(db_session, alice), _pitch(db_session, alice)
    _take(db_session, first, 1, **_ended(2))
    _take(db_session, second, 1, **_ended(3))

    rows = client.get("/admin/api/users", auth=ADMIN).json()

    assert [row["name"] for row in rows] == ["앨리스", "연습 안 함"]
    assert rows[0]["pitch_count"] == 2
    assert rows[0]["take_count"] == 2
    assert rows[0]["total_practice_sec"] == pytest.approx(5 * 60)
    assert rows[1]["take_count"] == 0
    assert rows[1]["last_take_at"] is None


@pytest.mark.usefixtures("admin_account")
def test_negative_practice_time_is_left_out_everywhere(
    client: TestClient, db_session: Session
) -> None:
    """시작은 FE 시계, 자동 종료는 서버 시계라 어긋나면 끝이 시작보다 앞선다.

    어디서도 세지 않는다.
    """
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    started = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    _take(db_session, pitch, 1, **_ended(2))
    _take(
        db_session,
        pitch,
        2,
        status="ANALYZING",
        started_at=started,
        ended_at=started - timedelta(seconds=5),
        end_reason="SILENCE",
    )

    stats = client.get("/admin/api/stats", auth=ADMIN).json()["practice_time"]
    [user] = client.get("/admin/api/users", auth=ADMIN).json()

    assert stats["measured_takes"] == 1
    assert stats["total_sec"] == pytest.approx(2 * 60)
    assert user["total_practice_sec"] == pytest.approx(2 * 60)
