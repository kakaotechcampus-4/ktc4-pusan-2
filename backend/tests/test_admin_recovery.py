"""관리자 콘솔의 복구 동작 (/admin/api/takes/{take_id}/...) — 로그인 · CSRF 헤더 · 조건 확인."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.coach.finalize import STALE_FINALIZING
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin.dependencies import ACTION_HEADER
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeMetricSecond
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC
from tests.test_admin import (  # noqa: F401
    ADMIN,
    _final_segment,
    _pitch,
    _take,
    _user,
    admin_account,
)

ACTION = {ACTION_HEADER: "1"}


@pytest.fixture
def recoveries(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """띄운 보류 복구 작업. 실제 AI 를 부르지 않게 바꿔 끼운다."""
    started: list[uuid.UUID] = []

    def recover_held(take_id: uuid.UUID) -> object:
        started.append(take_id)
        return object()

    monkeypatch.setattr(analysis_runner, "recover_held", recover_held)
    return started


def _held(db: Session, *, candidate: bool = True, **fields):
    take = _take(
        db,
        _pitch(db, _user(db, "앨리스")),
        1,
        status="ANALYZING",
        analysis_stage="SCORING",
        analysis_hold_reason="COVERAGE_RESCORE_FAILED",
        **fields,
    )
    if candidate:
        db.add(
            TakeAIResults(
                take_id=take.id,
                kind="SCRIPT_COVERAGE_PENDING",
                payload={"base_revision": 0},
                meta=None,
            )
        )
        db.flush()
    return take


@pytest.mark.usefixtures("admin_account")
def test_held_take_rescore_starts_in_the_background(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID]
) -> None:
    take = _held(db_session)

    response = client.post(
        f"/admin/api/takes/{take.id}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 202
    assert recoveries == [take.id]


@pytest.mark.usefixtures("admin_account")
def test_recovery_needs_the_console_header(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID]
) -> None:
    """Basic 로그인은 다른 사이트의 요청에도 붙는다. 콘솔이 붙이는 헤더가 없으면 막는다."""
    take = _held(db_session)
    path = f"/admin/api/takes/{take.id}/coverage/rescore"

    for headers in ({}, {ACTION_HEADER: "0"}):
        response = client.post(path, auth=ADMIN, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "ADMIN_ACTION_FORBIDDEN"
    assert recoveries == []


@pytest.mark.usefixtures("admin_account")
def test_recovery_asks_for_login_first(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID]
) -> None:
    take = _held(db_session)

    response = client.post(f"/admin/api/takes/{take.id}/coverage/rescore", headers=ACTION)

    assert response.status_code == 401
    assert recoveries == []


def test_recovery_is_hidden_without_admin_account(client: TestClient) -> None:
    response = client.post(
        f"/admin/api/takes/{uuid.uuid4()}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 404


@pytest.mark.usefixtures("admin_account")
@pytest.mark.parametrize(
    "fields",
    [
        {"analysis_hold_reason": None},
        {"status": "COMPLETED", "analysis_stage": "DONE", "analysis_hold_reason": None},
    ],
    ids=["not-held", "completed"],
)
def test_rescore_of_a_take_that_is_not_held_conflicts(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID], fields: dict
) -> None:
    take = _held(db_session)
    for name, value in fields.items():
        setattr(take, name, value)
    db_session.flush()

    response = client.post(
        f"/admin/api/takes/{take.id}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "TAKE_NOT_RECOVERABLE",
        "message": "리뷰를 보류한 연습이 아닙니다.",
        "detail": None,
    }
    assert recoveries == []


@pytest.mark.usefixtures("admin_account")
def test_rescore_without_candidate_conflicts(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID]
) -> None:
    take = _held(db_session, candidate=False)

    response = client.post(
        f"/admin/api/takes/{take.id}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_NOT_RECOVERABLE"


@pytest.mark.usefixtures("admin_account")
def test_rescore_while_a_recovery_holds_the_mark_conflicts(
    client: TestClient, db_session: Session, recoveries: list[uuid.UUID]
) -> None:
    take = _held(db_session)
    # 앞서 누른 복구가 표식을 잡고 AI 를 기다린다
    take.coverage_requested_at = db_session.scalar(select(func.now()))
    db_session.flush()

    response = client.post(
        f"/admin/api/takes/{take.id}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RECOVERY_RUNNING"
    assert recoveries == []


@pytest.mark.usefixtures("admin_account")
def test_rescore_while_the_take_is_analyzed_in_this_process_conflicts(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    take = _held(db_session)
    monkeypatch.setattr(analysis_runner, "recover_held", lambda take_id: None)

    response = client.post(
        f"/admin/api/takes/{take.id}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RECOVERY_RUNNING"


@pytest.mark.usefixtures("admin_account")
def test_rescore_of_unknown_take_is_not_found(
    client: TestClient, recoveries: list[uuid.UUID]
) -> None:
    response = client.post(
        f"/admin/api/takes/{uuid.uuid4()}/coverage/rescore", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 404
    assert recoveries == []


# ── finalize 다시 만들기 ─────────────────────────────────────────


class RestartSpy:
    def __init__(self) -> None:
        self.restarted: list[uuid.UUID] = []

    def recovery_request(self, db: Session, take_id: uuid.UUID) -> dict | None:
        return None

    def restart(self, take_id: uuid.UUID) -> None:
        self.restarted.append(take_id)


@pytest.fixture
def finalize_recovery(monkeypatch: pytest.MonkeyPatch) -> RestartSpy:
    spy = RestartSpy()
    monkeypatch.setattr(hooks, "_finalize_recovery", spy)
    return spy


def _finalize_failed(db: Session, *, raw: bool = True, ended_days_ago: float = 1, **fields) -> Take:
    take = _take(
        db,
        _pitch(db, _user(db, "앨리스")),
        1,
        status=fields.pop("status", "FAILED"),
        analysis_stage=fields.pop("analysis_stage", "FINALIZING"),
        ended_at=datetime.now(UTC) - timedelta(days=ended_days_ago),
        **fields,
    )
    if raw:
        db.add(TakeMetricSecond(take_id=take.id, t_ms=1_000, stt_status="ok"))
        db.flush()
    return take


@pytest.mark.usefixtures("admin_account")
def test_failed_finalize_is_reopened_and_restarted(
    client: TestClient, db_session: Session, finalize_recovery: RestartSpy
) -> None:
    take = _finalize_failed(db_session)

    response = client.post(f"/admin/api/takes/{take.id}/finalize/retry", auth=ADMIN, headers=ACTION)

    assert response.status_code == 202
    assert finalize_recovery.restarted == [take.id]
    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "FINALIZING")


class FailingRestart(RestartSpy):
    def restart(self, take_id: uuid.UUID) -> None:
        raise RuntimeError("finalize 를 띄우지 못했다")


@pytest.mark.usefixtures("admin_account")
def test_finalize_that_cannot_be_started_is_left_to_the_sweep(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """되돌린 뒤 finalize 를 띄우지 못하면 503 으로 따로 알린다. Take 는 Take 결과를 기다리는 중으로
    남아 finalize 주기 작업이 이어받는다."""
    monkeypatch.setattr(hooks, "_finalize_recovery", FailingRestart())
    take = _finalize_failed(db_session)

    response = client.post(f"/admin/api/takes/{take.id}/finalize/retry", auth=ADMIN, headers=ACTION)

    assert response.status_code == 503
    assert response.json()["code"] == "FINALIZE_NOT_STARTED"
    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "FINALIZING")

    def swept(at: datetime) -> list[uuid.UUID]:
        # finalize 주기 작업(`finalize.sweep_forever`)이 `at` 에 돌 때 다시 띄우는 Take
        return coach_service.finalizing_takes(db_session, at - STALE_FINALIZING)

    reopened_at = take.analysis_stage_at
    assert take.id not in swept(reopened_at + STALE_FINALIZING - timedelta(seconds=1))
    assert take.id in swept(reopened_at + STALE_FINALIZING + timedelta(seconds=1))


@pytest.mark.usefixtures("admin_account")
@pytest.mark.parametrize(
    "fields, message",
    [
        (
            {"status": "COMPLETED", "analysis_stage": "DONE"},
            "Take 결과를 만들지 못한 연습이 아닙니다.",
        ),
        (
            {"analysis_stage": None, "end_reason": "ABANDONED"},
            "Take 결과를 만들지 못한 연습이 아닙니다.",
        ),
        (
            {"ended_days_ago": 8},
            "원자료 사용에 동의하지 않은 연습이라 끝난 지 7일이 지나 다시 만들 수 없습니다.",
        ),
        ({"raw": False}, "1초 원자료가 없어 다시 만들 수 없습니다."),
    ],
    ids=["completed", "abandoned", "expired", "no-raw-data"],
)
def test_finalize_retry_says_why_it_cannot_reopen(
    client: TestClient,
    db_session: Session,
    finalize_recovery: RestartSpy,
    fields: dict,
    message: str,
) -> None:
    take = _finalize_failed(db_session, **fields)

    response = client.post(f"/admin/api/takes/{take.id}/finalize/retry", auth=ADMIN, headers=ACTION)

    assert response.status_code == 409
    assert response.json() == {"code": "TAKE_NOT_RECOVERABLE", "message": message, "detail": None}
    assert finalize_recovery.restarted == []


@pytest.mark.usefixtures("admin_account")
def test_finalize_retry_needs_the_console_header(
    client: TestClient, db_session: Session, finalize_recovery: RestartSpy
) -> None:
    take = _finalize_failed(db_session)

    response = client.post(f"/admin/api/takes/{take.id}/finalize/retry", auth=ADMIN)

    assert response.status_code == 403
    assert finalize_recovery.restarted == []
    db_session.refresh(take)
    assert take.status == "FAILED"


@pytest.mark.usefixtures("admin_account")
def test_finalize_retry_of_unknown_take_is_not_found(
    client: TestClient, finalize_recovery: RestartSpy
) -> None:
    response = client.post(
        f"/admin/api/takes/{uuid.uuid4()}/finalize/retry", auth=ADMIN, headers=ACTION
    )

    assert response.status_code == 404
    assert finalize_recovery.restarted == []


# ── 멈춘 연습 끝내기 ────────────────────────────────────────────


class EndStuckSpy:
    """실시간 hook. 스트림 · 타이머 정리는 `tests/realtime/test_ws_abandon.py` 가 본다."""

    def __init__(self, ends: bool = True) -> None:
        self.ends = ends
        self.asked: list[tuple[uuid.UUID, datetime]] = []

    def end_stuck(self, take_id: uuid.UUID, last_active_at: datetime) -> bool:
        self.asked.append((take_id, last_active_at))
        return self.ends


@pytest.fixture
def live_takes(monkeypatch: pytest.MonkeyPatch) -> EndStuckSpy:
    spy = EndStuckSpy()
    monkeypatch.setattr(hooks, "_live_takes", spy)
    return spy


def _long_ago() -> datetime:
    """'멈춘 연습' 표에 들어가는 마지막 활동 시각 (이탈 대기 + 10분보다 앞)."""
    return datetime.now(UTC) - timedelta(seconds=ABANDON_AFTER_SEC) - timedelta(minutes=11)


@pytest.mark.usefixtures("admin_account")
def test_stuck_take_is_ended_from_its_last_activity(
    client: TestClient, db_session: Session, live_takes: EndStuckSpy
) -> None:
    created = _long_ago() - timedelta(minutes=5)
    take = _take(
        db_session,
        _pitch(db_session, _user(db_session, "앨리스")),
        1,
        status="RUNNING",
        created_at=created,
    )
    # 마지막 활동은 마지막 확정 전사를 저장한 시각이다
    last_final = _long_ago()
    _final_segment(db_session, take, last_final)

    response = client.post(f"/admin/api/takes/{take.id}/abandon", auth=ADMIN, headers=ACTION)

    assert response.status_code == 204
    assert live_takes.asked == [(take.id, last_final)]


@pytest.mark.usefixtures("admin_account")
@pytest.mark.parametrize(
    "status, created_at",
    [
        ("RUNNING", lambda: datetime.now(UTC)),
        ("ANALYZING", _long_ago),
        ("READY", _long_ago),
    ],
    ids=["recently-active", "analyzing", "ready"],
)
def test_take_that_is_not_stuck_is_not_ended(
    client: TestClient, db_session: Session, live_takes: EndStuckSpy, status: str, created_at
) -> None:
    take = _take(
        db_session,
        _pitch(db_session, _user(db_session, "앨리스")),
        1,
        status=status,
        created_at=created_at(),
    )

    response = client.post(f"/admin/api/takes/{take.id}/abandon", auth=ADMIN, headers=ACTION)

    assert response.status_code == 409
    assert response.json()["message"] == "멈춘 연습이 아닙니다."
    assert live_takes.asked == []


@pytest.mark.usefixtures("admin_account")
def test_stuck_take_that_the_stream_side_refuses_conflicts(
    client: TestClient, db_session: Session, live_takes: EndStuckSpy
) -> None:
    """연결이 다시 붙었거나 그 사이 끝났으면 실시간 쪽이 끝내지 않는다."""
    live_takes.ends = False
    take = _take(
        db_session,
        _pitch(db_session, _user(db_session, "앨리스")),
        1,
        status="RUNNING",
        created_at=_long_ago(),
    )

    response = client.post(f"/admin/api/takes/{take.id}/abandon", auth=ADMIN, headers=ACTION)

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_NOT_RECOVERABLE"


@pytest.mark.usefixtures("admin_account")
def test_ending_a_stuck_take_needs_the_console_header(
    client: TestClient, db_session: Session, live_takes: EndStuckSpy
) -> None:
    take = _take(
        db_session,
        _pitch(db_session, _user(db_session, "앨리스")),
        1,
        status="RUNNING",
        created_at=_long_ago(),
    )

    response = client.post(f"/admin/api/takes/{take.id}/abandon", auth=ADMIN)

    assert response.status_code == 403
    assert live_takes.asked == []


@pytest.mark.usefixtures("admin_account")
def test_ending_an_unknown_take_is_not_found(client: TestClient, live_takes: EndStuckSpy) -> None:
    response = client.post(f"/admin/api/takes/{uuid.uuid4()}/abandon", auth=ADMIN, headers=ACTION)

    assert response.status_code == 404
    assert live_takes.asked == []
