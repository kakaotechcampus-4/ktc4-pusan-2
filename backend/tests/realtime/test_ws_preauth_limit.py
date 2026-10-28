"""발표 WS 의 인증 전 연결 수 상한 (IP 별 · 전체)."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from pitch_coach_backend.realtime.connection import handshake as handshake_module
from pitch_coach_backend.realtime.connection.handshake import (
    CLOSE_TRY_AGAIN_LATER,
    Handshake,
    client_ip,
    pending_auth,
)
from tests.realtime.conftest import WS_PATH, FakeSttAdapter, FakeWebSocket, handshake, stop


class SilentWebSocket(FakeWebSocket):
    """연결만 하고 auth 를 보내지 않는다. `hang_up` 하면 끊는다."""

    def __init__(self, ip: str) -> None:
        super().__init__()
        self.headers = {"x-forwarded-for": ip}
        self.accepted = False
        self.hung_up = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        await self.hung_up.wait()
        return {"type": "websocket.disconnect"}


def _admit(ws: FakeWebSocket) -> asyncio.Task[bool]:
    shake = Handshake(ws, take_id=uuid.uuid7(), db=None, needs_coach_context=lambda: False)  # type: ignore[arg-type]
    return asyncio.create_task(shake.admit())


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _no_leftover_pending():
    yield
    assert pending_auth.total == 0, "인증 전 연결 수를 돌려주지 않았다"
    assert not pending_auth.per_ip


def test_client_ip_is_the_first_forwarded_address():
    ws = SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.9"))
    assert client_ip(ws) == "10.0.0.9"  # type: ignore[arg-type]
    ws.headers = {"x-forwarded-for": "203.0.113.7, 172.18.0.2"}
    assert client_ip(ws) == "203.0.113.7"  # type: ignore[arg-type]
    ws.headers = {"x-forwarded-for": " "}
    assert client_ip(ws) == "10.0.0.9"  # type: ignore[arg-type]
    assert client_ip(SimpleNamespace(headers={}, client=None)) == "unknown"  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_connections_over_the_per_ip_limit_are_closed_before_accept(
    monkeypatch: pytest.MonkeyPatch,
):
    """auth 를 보내지 않는 연결은 IP 하나에 상한까지만 받는다. 넘은 연결은 받기 전에 닫는다
    (브라우저에는 연결 실패로 보여 FE 가 백오프로 다시 붙는다). 다른 IP 는 그대로 받는다."""
    monkeypatch.setattr(handshake_module, "MAX_PENDING_AUTH_PER_IP", 2)
    sockets = [SilentWebSocket("203.0.113.7") for _ in range(3)]
    other = SilentWebSocket("198.51.100.1")
    tasks = [_admit(ws) for ws in (*sockets, other)]
    await _settle()

    assert [ws.accepted for ws in sockets] == [True, True, False]
    assert sockets[2].closed == (CLOSE_TRY_AGAIN_LATER, "too many connections")
    assert other.accepted
    assert tasks[2].done() and tasks[2].result() is False

    for ws in (*sockets, other):
        ws.hung_up.set()
    assert await asyncio.gather(*tasks) == [False] * 4


@pytest.mark.anyio
async def test_connections_over_the_total_limit_are_closed_before_accept(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(handshake_module, "MAX_PENDING_AUTH", 3)
    sockets = [SilentWebSocket(f"203.0.113.{i}") for i in range(4)]
    tasks = [_admit(ws) for ws in sockets]
    await _settle()

    assert [ws.accepted for ws in sockets] == [True, True, True, False]

    for ws in sockets:
        ws.hung_up.set()
    await asyncio.gather(*tasks)


@pytest.mark.anyio
async def test_slots_come_back_after_the_auth_timeout(monkeypatch: pytest.MonkeyPatch):
    """auth 를 끝내 보내지 않아 닫은 연결의 자리는 돌려준다. 그 IP 의 다음 연결을 받는다."""
    monkeypatch.setattr(handshake_module, "MAX_PENDING_AUTH_PER_IP", 1)
    monkeypatch.setattr(handshake_module, "AUTH_TIMEOUT_SEC", 0.05)
    silent = SilentWebSocket("203.0.113.7")

    assert await _admit(silent) is False
    assert json.loads(silent.sent[0])["code"] == "UNAUTHORIZED"
    assert pending_auth.total == 0

    again = SilentWebSocket("203.0.113.7")
    task = _admit(again)
    await _settle()
    assert again.accepted
    again.hung_up.set()
    await task


def test_endpoint_refuses_before_accept_and_normal_connections_still_work(
    client: TestClient, stt: FakeSttAdapter, token: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(handshake_module, "MAX_PENDING_AUTH", 0)
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(WS_PATH):
        pass
    assert refused.value.code == CLOSE_TRY_AGAIN_LATER

    monkeypatch.setattr(handshake_module, "MAX_PENDING_AUTH", 1)
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        # 인증을 마친 연결은 인증 전 연결로 세지 않는다
        assert pending_auth.total == 0
        stop(ws)
