"""관리자 콘솔 (/admin) — 로그인, 화면 파일, 집계 API."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
    StandardAnalyses,
    Standards,
)
from pitch_coach_backend.module.take.entity import (
    Calibration,
    CoachEvent,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
    TakeSummary,
    TakeTranscriptSegment,
)
from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC
from pitch_coach_backend.module.user.entity import User

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
        # 아이디 칸에 비밀번호를 잘못 넣은 경우다. 입력한 아이디도 남기지 않는다
        auth=("secret-typed-as-username", "wrong"),
        headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Uri": "/admin/logs/"},
    )

    [record] = caplog.records
    assert "ip=203.0.113.7" in record.getMessage()
    assert "path=/admin/logs/" in record.getMessage()
    assert "secret-typed-as-username" not in record.getMessage()
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
    # 시작 전에 떠난 Take 는 ready_sweep 이 끝낸다. 넣지 않는다
    _take(db_session, pitch, 4, status="READY", created_at=long_ago)
    # 분석 중인 Take 는 분석이 멈춘 연습에서 따로 본다. 넣지 않는다
    _take(db_session, pitch, 5, status="ANALYZING", created_at=long_ago)

    rows = client.get("/admin/api/takes/stuck", auth=ADMIN).json()

    assert [row["id"] for row in rows] == [str(stuck.id)]


@pytest.mark.usefixtures("admin_account")
def test_stalled_analyses_are_held_or_past_resume_sweep(
    client: TestClient, db_session: Session
) -> None:
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    now = datetime.now(UTC)

    def analyzing(number: int, stage: str | None, minutes_ago: float | None, **fields) -> Take:
        stage_at = None if minutes_ago is None else now - timedelta(minutes=minutes_ago)
        return _take(
            db_session,
            pitch,
            number,
            status=fields.pop("status", "ANALYZING"),
            analysis_stage=stage,
            analysis_stage_at=stage_at,
            **fields,
        )

    held = analyzing(1, "SCORING", 1, analysis_hold_reason="COVERAGE_RESCORE_FAILED")
    finalizing = analyzing(2, "FINALIZING", 16)
    confirming = analyzing(3, "AWAITING_CONFIRMATION", 26)
    unknown_at = analyzing(4, None, None)
    analyzing(5, "SCORING", 5)
    analyzing(6, "AWAITING_CONFIRMATION", 20)
    analyzing(7, "DONE", 60, status="COMPLETED", review_status="DONE")
    analyzing(8, "FINALIZING", 60, status="FAILED")

    rows = client.get("/admin/api/takes/stalled-analysis", auth=ADMIN).json()

    assert [row["id"] for row in rows] == [
        str(unknown_at.id),
        str(confirming.id),
        str(finalizing.id),
        str(held.id),
    ]
    assert rows[-1]["analysis_hold_reason"] == "COVERAGE_RESCORE_FAILED"


@pytest.mark.usefixtures("admin_account")
def test_takes_carry_analysis_state(client: TestClient, db_session: Session) -> None:
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    stage_at = datetime(2026, 10, 22, 1, 30, tzinfo=UTC)
    _take(
        db_session,
        pitch,
        1,
        **_ended(3),
        analysis_stage="REVIEWING",
        analysis_stage_at=stage_at,
        review_status="FAILED",
    )

    [row] = client.get("/admin/api/takes", auth=ADMIN).json()

    assert row["pitch_id"] == str(pitch.id)
    assert row["analysis_stage"] == "REVIEWING"
    assert datetime.fromisoformat(row["analysis_stage_at"]) == stage_at
    assert row["review_status"] == "FAILED"
    assert row["analysis_hold_reason"] is None


@pytest.mark.usefixtures("admin_account")
def test_pitch_ai_shows_scripts_standards_and_plans(
    client: TestClient, db_session: Session
) -> None:
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    script = ScriptVersion(
        pitch_id=pitch.id,
        version=2,
        content="대본",
        parse_status="DONE",
        rubric_status="PARTIAL",
        rubric_retry_used=True,
    )
    db_session.add(script)
    db_session.flush()
    db_session.add_all([
        ScriptSlide(script_version_id=script.id, slide_number=1, full_content="일", rubric={}),
        ScriptSlide(script_version_id=script.id, slide_number=2, full_content="이"),
        ScriptSlide(script_version_id=script.id, slide_number=3, full_content="삼"),
        Standards(pitch_id=pitch.id, version=1, position=1, title="천천히"),
        Standards(pitch_id=pitch.id, version=1, position=2, title="청중 보기"),
        Standards(pitch_id=pitch.id, version=2, position=1, title="또박또박"),
        StandardAnalyses(
            pitch_id=pitch.id,
            version=1,
            targets=[],
            excluded=[],
            script_used=False,
            meta={"model": "m"},
        ),
    ])
    requested = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    db_session.add_all([
        CoachingPlan(
            pitch_id=pitch.id,
            requested_at=requested,
            payload={"coaching_plan": {}, "fallback_reason": None},
            meta={"model": "planner-model"},
        ),
        CoachingPlan(
            pitch_id=pitch.id,
            requested_at=requested + timedelta(minutes=1),
            payload={"coaching_plan": {}, "fallback_reason": "LLM_TIMEOUT"},
            meta={"model": "fallback"},
        ),
    ])
    db_session.flush()

    body = client.get(f"/admin/api/pitches/{pitch.id}/ai", auth=ADMIN).json()

    assert [s["version"] for s in body["scripts"]] == [2, 1]
    latest = body["scripts"][0]
    assert latest["rubric_status"] == "PARTIAL"
    assert (latest["slide_count"], latest["rubric_slide_count"]) == (3, 1)
    assert latest["missing_rubric_slides"] == [2, 3]
    assert body["scripts"][1]["missing_rubric_slides"] == []
    assert [(s["version"], s["criteria_count"]) for s in body["standards"]] == [(2, 1), (1, 2)]
    assert body["standards"][0]["analyzed_at"] is None
    assert body["standards"][1]["script_used"] is False
    assert [(p["fallback_reason"], p["model"]) for p in body["coaching_plans"]] == [
        ("LLM_TIMEOUT", "fallback"),
        (None, "planner-model"),
    ]


@pytest.mark.usefixtures("admin_account")
def test_pitch_ai_of_unknown_pitch_is_not_found(client: TestClient) -> None:
    response = client.get(f"/admin/api/pitches/{uuid.uuid4()}/ai", auth=ADMIN)

    assert response.status_code == 404


@pytest.mark.usefixtures("admin_account")
def test_take_live_returns_rows_after_cursors_in_order(
    client: TestClient, db_session: Session
) -> None:
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    take = _take(db_session, pitch, 1, status="RUNNING")
    other = _take(db_session, pitch, 2, status="RUNNING")
    for seq in (3, 1, 2):
        db_session.add(
            TakeTranscriptSegment(
                take_id=take.id,
                seq=seq,
                stt_session_no=1,
                start_ms=seq * 1000,
                end_ms=seq * 1000 + 500,
                transcript=f"문장 {seq}",
                words=[],
                confidence=0.9,
                speech_final=True,
            )
        )
    for t_ms in (3000, 1000, 2000):
        db_session.add(
            TakeMetricSecond(
                take_id=take.id,
                t_ms=t_ms,
                slide_number=1,
                gaze={"t_ms": t_ms - 1000, "duration_ms": 1000},
                voice=None,
                stt_status="ok",
            )
        )
    db_session.add(TakeMetricSecond(take_id=other.id, t_ms=4000, stt_status="ok"))
    db_session.flush()

    first = client.get(f"/admin/api/takes/{take.id}/live", auth=ADMIN).json()
    rest = client.get(
        f"/admin/api/takes/{take.id}/live",
        params={"after_seq": 1, "after_t_ms": 2000},
        auth=ADMIN,
    ).json()

    assert first["status"] == "RUNNING"
    assert [s["seq"] for s in first["segments"]] == [1, 2, 3]
    assert first["segments"][0]["transcript"] == "문장 1"
    assert "words" not in first["segments"][0]
    assert [s["t_ms"] for s in first["seconds"]] == [1000, 2000, 3000]
    assert first["seconds"][0]["gaze"] == {"t_ms": 0, "duration_ms": 1000}
    assert first["seconds"][0]["voice"] is None
    assert [s["seq"] for s in rest["segments"]] == [2, 3]
    assert [s["t_ms"] for s in rest["seconds"]] == [3000]


@pytest.mark.usefixtures("admin_account")
def test_take_live_of_unknown_take_is_not_found(client: TestClient) -> None:
    response = client.get(f"/admin/api/takes/{uuid.uuid4()}/live", auth=ADMIN)

    assert response.status_code == 404
    assert response.json()["code"] == "TAKE_NOT_FOUND"


@pytest.mark.usefixtures("admin_account")
def test_take_results_show_ai_results_events_and_summary(
    client: TestClient, db_session: Session
) -> None:
    take = _take(db_session, _pitch(db_session, _user(db_session, "앨리스")), 1, **_ended(3))
    db_session.add_all([
        TakeAIResults(
            take_id=take.id,
            kind="REVIEW",
            payload={"overall_summary": "요약", "fallback_reason": "LLM_TIMEOUT"},
            meta={"model": "review-model"},
            revision=1,
        ),
        TakeAIResults(
            take_id=take.id,
            kind="SCRIPT_COVERAGE",
            payload={"slides": []},
            meta={"model": "coverage-model"},
        ),
        TakeAIResults(
            take_id=take.id,
            kind="TAKE_RESULT",
            payload={
                "replayed": True,
                "criteria_changed": False,
                "areas": {"GAZE": {"measured_ratio": 0.9}, "SPEED": {"measured_ratio": None}},
            },
            meta={"schema_version": "1.0"},
        ),
        CoachEvent(
            take_id=take.id, seq=2, kind="INTERVENTION", event_id="e2", t_ms=5_000,
            payload={"kind": "INTERVENTION"}, meta={},
        ),
        CoachEvent(
            take_id=take.id, seq=1, kind="ISSUE_OPENED", event_id="e1", t_ms=3_000,
            payload={"kind": "ISSUE_OPENED"}, meta={},
        ),
        TakeSummary(take_id=take.id, average_cpm=320.5, filler_count=4, score=None),
    ])
    db_session.flush()

    body = client.get(f"/admin/api/takes/{take.id}/results", auth=ADMIN).json()

    result, coverage, review = body["ai_results"]
    assert [result["kind"], coverage["kind"], review["kind"]] == [
        "TAKE_RESULT",
        "SCRIPT_COVERAGE",
        "REVIEW",
    ]
    assert (result["replayed"], result["criteria_changed"]) == (True, False)
    assert result["measured_ratios"] == {"GAZE": 0.9, "SPEED": None}
    assert result["fallback_reason"] is None
    assert coverage["model"] == "coverage-model"
    assert coverage["replayed"] is None
    assert (review["fallback_reason"], review["model"], review["revision"]) == (
        "LLM_TIMEOUT",
        "review-model",
        1,
    )
    assert [(e["seq"], e["t_ms"]) for e in body["coach_events"]] == [(1, 3_000), (2, 5_000)]
    assert body["summary"]["average_cpm"] == 320.5
    assert body["summary"]["filler_count"] == 4


@pytest.mark.usefixtures("admin_account")
def test_take_results_without_anything_yet(client: TestClient, db_session: Session) -> None:
    take = _take(db_session, _pitch(db_session, _user(db_session, "앨리스")), 1)

    body = client.get(f"/admin/api/takes/{take.id}/results", auth=ADMIN).json()

    assert body == {"ai_results": [], "coach_events": [], "summary": None}


@pytest.mark.usefixtures("admin_account")
def test_take_results_of_unknown_take_is_not_found(client: TestClient) -> None:
    response = client.get(f"/admin/api/takes/{uuid.uuid4()}/results", auth=ADMIN)

    assert response.status_code == 404


@pytest.mark.usefixtures("admin_account")
def test_take_inputs_show_what_the_judges_read(client: TestClient, db_session: Session) -> None:
    take = _take(
        db_session,
        _pitch(db_session, _user(db_session, "앨리스")),
        1,
        **_ended(3),
        script_mode="KEYWORD",
        standard_version=2,
        mission_ids=["m-1"],
        utterance_ends=[1_200, 3_400],
    )
    words = [{"word": "안녕", "start_ms": 100, "end_ms": 400, "final_at_ms": 900}]
    db_session.add_all([
        TakeTranscriptSegment(
            take_id=take.id, seq=1, stt_session_no=1, start_ms=100, end_ms=400,
            transcript="안녕", words=words, confidence=0.9, speech_final=True, final_at_ms=900,
        ),
        TakeSlideEvent(take_id=take.id, started_ms=2_000, slide_number=2),
        TakeSlideEvent(take_id=take.id, started_ms=0, slide_number=1),
        Calibration(
            take_id=take.id, face_detected=True, mic_detected=True, base_volume=40,
            base_level_db=-32.5, gaze_confidence=0.8, result={"engine": "v2"},
        ),
    ])
    db_session.flush()

    body = client.get(f"/admin/api/takes/{take.id}/inputs", auth=ADMIN).json()

    assert body["script_mode"] == "KEYWORD"
    assert body["standard_version"] == 2
    assert body["mission_ids"] == ["m-1"]
    assert body["utterance_ends"] == [1_200, 3_400]
    assert body["slide_events"] == [
        {"started_ms": 0, "slide_number": 1},
        {"started_ms": 2_000, "slide_number": 2},
    ]
    [segment] = body["segments"]
    assert (segment["final_at_ms"], segment["words"]) == (900, words)
    assert body["calibration"]["base_level_db"] == -32.5
    assert body["calibration"]["result"] == {"engine": "v2"}


@pytest.mark.usefixtures("admin_account")
def test_take_export_wraps_the_recovery_request_with_results(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    take = _take(db_session, _pitch(db_session, _user(db_session, "앨리스")), 1, **_ended(3))
    db_session.add_all([
        TakeMetricSecond(take_id=take.id, t_ms=1_000, stt_status="ok"),
        TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload={"replayed": False}, meta={}),
    ])
    db_session.flush()
    asked: list[uuid.UUID] = []

    class FakeExport:
        def recovery_request(self, db: Session, take_id: uuid.UUID) -> dict | None:
            asked.append(take_id)
            return {"t_ms": 1_000, "replay": {"words": []}}

    monkeypatch.setattr(hooks, "_finalize_export", FakeExport())

    body = client.get(f"/admin/api/takes/{take.id}/export", auth=ADMIN).json()

    assert asked == [take.id]
    assert body == {
        "take_id": str(take.id),
        "raw_data_consent": False,
        "metric_second_count": 1,
        "request": {"t_ms": 1_000, "replay": {"words": []}},
        "results": {"TAKE_RESULT": {"payload": {"replayed": False}, "meta": {}, "revision": 0}},
    }


@pytest.mark.usefixtures("admin_account")
def test_take_inputs_and_export_of_unknown_take_are_not_found(client: TestClient) -> None:
    for path in ("inputs", "export"):
        response = client.get(f"/admin/api/takes/{uuid.uuid4()}/{path}", auth=ADMIN)

        assert response.status_code == 404


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


@pytest.mark.usefixtures("admin_account")
def test_practice_time_longer_than_a_take_can_run_is_cut_to_that_length(
    client: TestClient, db_session: Session
) -> None:
    """시각 범위 검사 전에 0001년 같은 시작 시각이 저장된 Take 다.

    Take 는 최대 길이를 넘겨 돌 수 없어서 그 길이로 센다.
    """
    pitch = _pitch(db_session, _user(db_session, "앨리스"))
    ended = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    _take(db_session, pitch, 1, **_ended(2))
    _take(
        db_session,
        pitch,
        2,
        status="ANALYZING",
        started_at=datetime(1, 1, 2, tzinfo=UTC),
        ended_at=ended,
    )

    stats = client.get("/admin/api/stats", auth=ADMIN).json()["practice_time"]
    [user] = client.get("/admin/api/users", auth=ADMIN).json()
    takes = client.get("/admin/api/takes", auth=ADMIN).json()

    assert stats["total_sec"] == pytest.approx(2 * 60 + 3600)
    assert user["total_practice_sec"] == pytest.approx(2 * 60 + 3600)
    assert max(take["elapsed_sec"] for take in takes) == pytest.approx(3600)
