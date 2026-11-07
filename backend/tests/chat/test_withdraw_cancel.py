"""회원 탈퇴가 그 사용자의 대화 턴을 끊는지(chat-agent.md 6-13). 턴을 실제로 돌린다."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.entity import ChatDailyUsage, ChatRun
from pitch_coach_backend.module.user.entity import User
from tests.chat.api_helpers import bearer
from tests.chat.test_run_cancel import (  # noqa: F401
    WAIT_SEC,
    HangingAgent,
    _send,
    _slot_taken,
    _wait_slot_free,
    hanging_agent,
    live_turns,
)
from tests.chat.test_turn_runner import session_factory  # noqa: F401

ME = "/api/users/me"

pytestmark = pytest.mark.usefixtures("chat_app")


def _withdraw(client: TestClient, user_id: uuid.UUID) -> None:
    response = client.delete(ME, headers=bearer(user_id))
    assert response.status_code == 204, response.text


def _service_usage(db_session: Session) -> ChatDailyUsage:
    db_session.expire_all()
    return db_session.scalar(select(ChatDailyUsage).where(ChatDailyUsage.user_id.is_(None)))


def _other_user(db_session: Session) -> uuid.UUID:
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    return other.id


@pytest.fixture
def stopped_runs(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """끊으려 한 run_id 를 모은다."""
    ids: list[uuid.UUID] = []
    monkeypatch.setattr(turn_runner, "cancel", ids.append)
    return ids


@pytest.mark.usefixtures("live_turns")
def test_withdraw_closes_the_open_ai_call_and_gives_the_chat_slot_back(
    client: TestClient,
    hanging_agent: HangingAgent,  # noqa: F811
    user_id: uuid.UUID,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_runner, "CHAT_SLOT_LIMIT", 1)
    run_id = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == run_id
    assert _slot_taken(client)

    _withdraw(client, user_id)

    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == run_id
    assert _wait_slot_free(client)
    # 실행 행은 회원과 함께 지워진다
    db_session.expire_all()
    assert db_session.get(ChatRun, uuid.UUID(run_id)) is None


@pytest.mark.usefixtures("live_turns")
def test_withdraw_leaves_other_users_turns_alone(
    client: TestClient,
    hanging_agent: HangingAgent,  # noqa: F811
    user_id: uuid.UUID,
    db_session: Session,
) -> None:
    other_id = _other_user(db_session)
    other = _send(client, other_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == other
    mine = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == mine

    _withdraw(client, user_id)

    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == mine
    assert hanging_agent.cancelled.empty()
    assert turn_runner._running.running(uuid.UUID(other))
    db_session.expire_all()
    assert db_session.get(ChatRun, uuid.UUID(other)).status == "RUNNING"


def test_withdraw_without_runs_stops_nothing(
    client: TestClient, user_id: uuid.UUID, stopped_runs: list[uuid.UUID]
) -> None:
    _withdraw(client, user_id)

    assert stopped_runs == []


def test_withdraw_gives_back_the_service_reservation_of_an_unsent_run(
    client: TestClient, user_id: uuid.UUID, db_session: Session, stopped_runs: list[uuid.UUID]
) -> None:
    # 턴 실행기를 막아 두어 실행은 AI 에 보내지 않은 채 QUEUED 다
    run_id = _send(client, user_id)
    assert _service_usage(db_session).reserved == 1

    _withdraw(client, user_id)

    assert _service_usage(db_session).reserved == 0
    assert stopped_runs == [uuid.UUID(run_id)]


@pytest.mark.usefixtures("live_turns")
def test_withdraw_keeps_the_service_count_of_a_sent_run(
    client: TestClient,
    hanging_agent: HangingAgent,  # noqa: F811
    user_id: uuid.UUID,
    db_session: Session,
) -> None:
    run_id = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == run_id
    # 보낼 때 예약을 이미 보낸 수로 바꿨다. 다른 사람의 예약 하나를 두어 잘못 돌려주는지 본다
    usage = _service_usage(db_session)
    assert (usage.reserved, usage.sent) == (0, 1)
    usage.reserved = 1
    db_session.commit()

    _withdraw(client, user_id)

    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == run_id
    usage = _service_usage(db_session)
    assert (usage.reserved, usage.sent) == (1, 1)
