"""관리자 콘솔의 복구 동작 (/admin/api/takes/{take_id}/...) — 로그인 · CSRF 헤더 · 조건 확인."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin.dependencies import ACTION_HEADER
from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeMetricSecond
from pitch_coach_backend.module.take.service import analysis_runner
from tests.test_admin import ADMIN, _pitch, _take, _user, admin_account  # noqa: F401

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
