"""실행 취소가 열린 AI 연결과 대화 자리를 돌려주는지(chat-agent.md 6-13). 턴을 실제로 돌린다."""

import asyncio
import json
import logging
import queue
import time
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.chat import ai_client, controller, turn_runner
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.user.entity import User
from tests.chat.api_helpers import bearer
from tests.chat.test_turn_runner import session_factory  # noqa: F401

WAIT_SEC = 10.0
HOME = {"route": "home", "pitch_id": None, "take_id": None, "screen": None}

# chat_app 픽스처가 start 를 막기 전의 원래 함수
_real_start = turn_runner.start

pytestmark = pytest.mark.usefixtures("chat_app")


class HangingAgent:
    """요청을 받고 답하지 않는 AI 서버. 어느 실행의 호출이 열리고 끊겼는지 알려 준다.

    턴은 시험 클라이언트의 이벤트 루프에서 돌고 시험은 다른 스레드에서 기다려 queue 를 쓴다.
    """

    def __init__(self) -> None:
        self.opened: queue.Queue[str] = queue.Queue()
        self.cancelled: queue.Queue[str] = queue.Queue()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        run_id = json.loads(request.content)["run"]["run_id"]
        self.opened.put(run_id)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.put(run_id)
            raise
        raise AssertionError("닿지 않는다")


@pytest.fixture
def hanging_agent(monkeypatch: pytest.MonkeyPatch) -> HangingAgent:
    fake = HangingAgent()
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(fake))
    monkeypatch.setattr(ai_client.httpx, "AsyncClient", client)
    return fake


@pytest.fixture
def live_turns(
    chat_app: list[uuid.UUID],
    hanging_agent: HangingAgent,
    session_factory: Callable[[], Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """메시지를 보내면 턴 실행기가 실제로 돈다."""
    monkeypatch.setattr(
        controller.turn_runner, "start", lambda run_id: _real_start(run_id, session_factory)
    )


def _send(client: TestClient, user_id: uuid.UUID) -> str:
    body = {
        "conversation_id": None,
        "text": "3장에서 왜 시선이 떨어져?",
        "page": HOME,
        "client_request_id": str(uuid.uuid4()),
    }
    response = client.post("/api/chat/messages", json=body, headers=bearer(user_id))
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _cancel(client: TestClient, user_id: uuid.UUID, run_id: str) -> dict[str, Any]:
    response = client.post(f"/api/chat/runs/{run_id}/cancel", headers=bearer(user_id))
    assert response.status_code == 200, response.text
    return response.json()


def _slot_taken(client: TestClient) -> bool:
    async def check() -> bool:
        return ai_slots.chat().locked()

    return client.portal.call(check)


def _wait_slot_free(client: TestClient) -> bool:
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        if not _slot_taken(client):
            return True
        time.sleep(0.01)
    return False


@pytest.mark.usefixtures("live_turns")
def test_cancel_closes_the_open_ai_call(
    client: TestClient, hanging_agent: HangingAgent, user_id: uuid.UUID, db_session: Session
) -> None:
    run_id = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == run_id

    assert _cancel(client, user_id, run_id)["status"] == "CANCELLED"

    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == run_id
    db_session.expire_all()
    run = db_session.get(ChatRun, uuid.UUID(run_id))
    assert run.status == "CANCELLED"
    assert run.error_code == "AGENT_CANCELLED"


@pytest.mark.usefixtures("live_turns")
def test_cancel_gives_the_chat_slot_back(
    client: TestClient,
    hanging_agent: HangingAgent,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 남은 자리가 하나면 취소한 턴이 돌려주어야 다음 턴이 AI 를 부른다. 자리 설정은 2 이상이어야
    # 대화가 켜지므로 두 자리 중 하나를 미리 쥔다
    monkeypatch.setattr(settings, "chat_ai_slots_per_process", 2)

    async def hold_one() -> None:
        await ai_slots.chat().acquire()

    client.portal.call(hold_one)
    first = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == first
    assert _slot_taken(client)

    _cancel(client, user_id, first)
    assert _wait_slot_free(client)

    second = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == second


@pytest.mark.usefixtures("live_turns")
def test_cancel_leaves_other_runs_alone(
    client: TestClient, hanging_agent: HangingAgent, user_id: uuid.UUID, db_session: Session
) -> None:
    cancelled = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == cancelled
    other = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == other

    _cancel(client, user_id, cancelled)

    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == cancelled
    assert hanging_agent.cancelled.empty()
    assert turn_runner._running.running(uuid.UUID(other))
    db_session.expire_all()
    assert db_session.get(ChatRun, uuid.UUID(other)).status == "RUNNING"


@pytest.mark.usefixtures("live_turns")
def test_user_cancel_and_app_stop_leave_different_logs(
    client: TestClient,
    hanging_agent: HangingAgent,
    user_id: uuid.UUID,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=turn_runner.logger.name)
    cancelled = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == cancelled
    stopped = _send(client, user_id)
    assert hanging_agent.opened.get(timeout=WAIT_SEC) == stopped

    _cancel(client, user_id, cancelled)
    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == cancelled
    client.portal.call(turn_runner.stop_all)
    assert hanging_agent.cancelled.get(timeout=WAIT_SEC) == stopped

    messages = [record.getMessage() for record in caplog.records]
    assert f"대화 턴을 멈춘다 (사용자 취소) run={cancelled}" in messages
    assert f"대화 턴을 멈춘다 (앱 종료) run={stopped}" in messages
    # 앱 종료는 실행 상태를 바꾸지 않는다. 다시 뜨면 복구 작업이 이어 간다
    db_session.expire_all()
    assert db_session.get(ChatRun, uuid.UUID(stopped)).status == "RUNNING"


@pytest.fixture
def stopped_runs(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """끊으려 한 run_id 를 모은다."""
    ids: list[uuid.UUID] = []
    monkeypatch.setattr(controller.turn_runner, "cancel", ids.append)
    return ids


def test_cancel_stops_the_turn_only_when_the_run_is_cancelled(
    client: TestClient,
    user_id: uuid.UUID,
    db_session: Session,
    stopped_runs: list[uuid.UUID],
) -> None:
    cancelled = _send(client, user_id)
    _cancel(client, user_id, cancelled)
    assert stopped_runs == [uuid.UUID(cancelled)]
    stopped_runs.clear()

    # 이미 다른 상태로 끝난 실행은 그 결과를 그대로 답하고 끊지 않는다
    finished = _send(client, user_id)
    db_session.get(ChatRun, uuid.UUID(finished)).status = "DONE"
    db_session.commit()
    assert _cancel(client, user_id, finished)["status"] == "DONE"
    assert stopped_runs == []


def test_cancel_of_someone_elses_run_stops_nothing(
    client: TestClient, user_id: uuid.UUID, db_session: Session, stopped_runs: list[uuid.UUID]
) -> None:
    run_id = _send(client, user_id)
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()

    response = client.post(f"/api/chat/runs/{run_id}/cancel", headers=bearer(other.id))

    assert response.status_code == 404
    assert stopped_runs == []
