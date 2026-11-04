"""관리자 콘솔의 코치 대화 운영 기록 · 진단 기록(chat-agent.md 12-3)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.limits import KST
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatDiagnostic, ChatRunLog
from pitch_coach_backend.module.user.entity import User

ADMIN = ("admin", "console-password")


@pytest.fixture(autouse=True)
def admin_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_username", ADMIN[0])
    monkeypatch.setattr(settings, "admin_password", ADMIN[1])


def _user(db: Session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", name="사용자")
    db.add(user)
    db.flush()
    return user


def _log(db: Session, user: User, day: Any, status: str, ms: int, **tokens: Any) -> None:
    db.add(
        ChatRunLog(
            run_id=uuid.uuid4(),
            day=day,
            user_id=user.id,
            status=status,
            ms=ms,
            input_tokens=tokens.get("input_tokens"),
            output_tokens=tokens.get("output_tokens"),
        )
    )
    db.flush()


def test_chat_days_count_finished_runs_per_day(
    client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    today = datetime.now(UTC).astimezone(KST).date()
    first, second = _user(db_session), _user(db_session)
    _log(db_session, first, today, "DONE", 1000, input_tokens=10, output_tokens=5)
    # 사용량을 모르는 실행은 0 으로 더하지 않고 따로 센다
    _log(db_session, first, today, "PARTIAL", 3000)
    _log(db_session, second, today, "FAILED", 2000, input_tokens=20, output_tokens=7)
    _log(db_session, second, today, "CANCELLED", 500, input_tokens=0, output_tokens=0)
    _log(
        db_session, second, today - timedelta(days=1), "DONE", 700, input_tokens=1, output_tokens=1
    )
    _log(
        db_session, second, today - timedelta(days=3), "DONE", 700, input_tokens=1, output_tokens=1
    )
    caplog.set_level(logging.INFO, logger="pitch_coach_backend.module.admin")

    response = client.get("/admin/api/chat/days?days=2", auth=ADMIN)

    assert response.status_code == 200
    rows = response.json()
    assert [row["day"] for row in rows] == [
        today.isoformat(),
        (today - timedelta(days=1)).isoformat(),
    ]
    assert rows[0] == {
        "day": today.isoformat(),
        "users": 2,
        "runs": 4,
        "done": 1,
        "partial": 1,
        "refused": 0,
        "failed": 1,
        "cancelled": 1,
        "expired": 0,
        "avg_ms": 1625,
        # 정렬하면 500 · 1000 · 2000 · 3000 이고 ceil(0.95 × 4) = 4 번째다
        "p95_ms": 3000,
        "input_tokens": 30,
        "output_tokens": 12,
        "tokens_missing": 1,
    }
    assert any("대화 운영 기록을 조회했다 days=2" in r.getMessage() for r in caplog.records)


def test_chat_days_p95_is_the_ceil_rank_of_sorted_times(
    client: TestClient, db_session: Session
) -> None:
    today = datetime.now(UTC).astimezone(KST).date()
    user = _user(db_session)
    for ms in range(100, 2001, 100):
        _log(db_session, user, today, "DONE", ms, input_tokens=1, output_tokens=1)

    [row] = client.get("/admin/api/chat/days?days=1", auth=ADMIN).json()

    # 20개 중 ceil(0.95 × 20) = 19 번째
    assert (row["runs"], row["avg_ms"], row["p95_ms"]) == (20, 1050, 1900)


def _diagnostic(db: Session, conversation: ChatConversation, run_id: uuid.UUID, call: int) -> None:
    db.add(
        ChatDiagnostic(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            run_id=run_id,
            turn_call=call,
            request_id=f"req-{call}",
            diagnostics={"stop_reason": "DONE", "model_calls": call},
            received_at=datetime(2026, 11, 5, 1, call, tzinfo=UTC),
        )
    )
    db.flush()


def test_chat_diagnostics_by_run_or_conversation(
    client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    user = _user(db_session)
    conversation = ChatConversation(user_id=user.id, title="질문")
    db_session.add(conversation)
    db_session.flush()
    first_run, second_run = uuid.uuid4(), uuid.uuid4()
    _diagnostic(db_session, conversation, first_run, 2)
    _diagnostic(db_session, conversation, first_run, 1)
    _diagnostic(db_session, conversation, second_run, 3)
    caplog.set_level(logging.INFO, logger="pitch_coach_backend.module.admin")

    by_run = client.get(f"/admin/api/chat/diagnostics?run_id={first_run}", auth=ADMIN).json()
    by_conversation = client.get(
        f"/admin/api/chat/diagnostics?conversation_id={conversation.id}", auth=ADMIN
    ).json()

    assert [row["turn_call"] for row in by_run["items"]] == [2, 1]
    assert by_run["items"][0]["request_id"] == "req-2"
    assert by_run["items"][0]["diagnostics"] == {"stop_reason": "DONE", "model_calls": 2}
    assert [row["turn_call"] for row in by_conversation["items"]] == [3, 2, 1]
    assert by_conversation["next_offset"] is None
    assert any(f"run={first_run}" in r.getMessage() for r in caplog.records)


def test_chat_diagnostics_are_paged_from_the_newest(
    client: TestClient, db_session: Session
) -> None:
    # 오래된 기록부터 자르면 가장 궁금한 최근 답의 기록이 빠진다
    conversation = ChatConversation(user_id=_user(db_session).id, title="질문")
    db_session.add(conversation)
    db_session.flush()
    for call in range(1, 4):
        _diagnostic(db_session, conversation, uuid.uuid4(), call)
    path = f"/admin/api/chat/diagnostics?conversation_id={conversation.id}&limit=2"

    first = client.get(path, auth=ADMIN).json()
    second = client.get(f"{path}&offset={first['next_offset']}", auth=ADMIN).json()

    assert [row["turn_call"] for row in first["items"]] == [3, 2]
    assert first["next_offset"] == 2
    assert [row["turn_call"] for row in second["items"]] == [1]
    assert second["next_offset"] is None


@pytest.mark.parametrize(
    "query",
    ["", f"?run_id={uuid.uuid4()}&conversation_id={uuid.uuid4()}"],
)
def test_chat_diagnostics_need_exactly_one_target(client: TestClient, query: str) -> None:
    response = client.get(f"/admin/api/chat/diagnostics{query}", auth=ADMIN)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("path", ["/admin/api/chat/days", "/admin/api/chat/diagnostics"])
def test_chat_admin_api_needs_admin_login(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
