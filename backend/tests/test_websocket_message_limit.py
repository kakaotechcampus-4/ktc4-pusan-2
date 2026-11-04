"""Docker 실행 인수로 실제 Uvicorn을 띄워 인증 전후 수신 크기 제한을 확인한다."""

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from uvicorn.main import main as uvicorn_cli
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.realtime.connection.handshake import Handshake
from pitch_coach_backend.realtime.dto import MAX_SECOND_RECORD_BYTES, MetricsMessage
from pitch_coach_backend.realtime.event_ingestion import (
    FRAME_HEADER,
    MAX_FRAME_BYTES,
    parse_audio_frame,
)

LIMIT = 65_536


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    command = next(line for line in dockerfile.read_text().splitlines() if line.startswith("CMD "))
    args = json.loads(command.removeprefix("CMD "))
    # 인수를 지워도 CLI 기본값으로 실행해 실제 통신에서 제한 누락을 잡는다.
    with uvicorn_cli.make_context("uvicorn", args[1:]) as context:
        size = context.params["ws_max_size"]
        protocol = context.params["ws"]

    app = FastAPI()

    @app.websocket("/ws")
    async def receive(ws: WebSocket) -> None:
        # JWT와 첫 메시지 인증은 실제로 실행하고 DB 인가만 대체한다.
        handshake = Handshake(
            ws, take_id=uuid.uuid4(), db=cast(Session, None), needs_coach_context=lambda: False
        )
        if not await handshake.admit():
            return
        await ws.send_text("ready")
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is None:
                    data = message["text"].encode("utf-8")
                await ws.send_text(str(len(data)))
        except WebSocketDisconnect:
            pass

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            ws=protocol,
            ws_max_size=size,
            lifespan="off",
            loop="asyncio",
            log_config=None,
            access_log=False,
        )
    )
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    with patch.object(Handshake, "_authorize", new=AsyncMock(return_value=True)):
        try:
            worker.start()
            deadline = time.monotonic() + 5
            while not server.started:
                assert worker.is_alive() and time.monotonic() < deadline
                time.sleep(0.01)
            yield f"ws://127.0.0.1:{port}/ws"
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            listener.close()
    assert not worker.is_alive()


def authenticate(ws) -> None:
    token = create_access_token(uuid.uuid4())
    ws.send(json.dumps({"type": "auth", "token": token}))
    assert ws.recv(timeout=2) == "ready"


def test_normal_audio_and_metrics_fit(server_url):
    frame = FRAME_HEADER.pack(1, 0) + bytes(MAX_FRAME_BYTES - FRAME_HEADER.size)
    assert parse_audio_frame(frame).duration_ms == 1_000
    record = {"t_ms": 0, "duration_ms": 1_000, "extension": ""}
    record["extension"] = "x" * (MAX_SECOND_RECORD_BYTES - len(json.dumps(record).encode()))
    metrics = MetricsMessage(
        type="metrics", t_ms=1_000, gaze=record, voice=record, slide_number=1
    ).model_dump_json()
    slide = json.dumps({"type": "slide", "t_ms": 0, "slide_number": 1})
    with connect(server_url, proxy=None, open_timeout=2, close_timeout=2) as ws:
        authenticate(ws)
        for payload in (frame, metrics, slide, '{"type":"stop"}'):
            ws.send(payload)
            expected = len(payload.encode()) if isinstance(payload, str) else len(payload)
            assert ws.recv(timeout=2) == str(expected)


@pytest.mark.parametrize("payload", [bytes(LIMIT), "x" * LIMIT, "é" * (LIMIT // 2)])
def test_exact_limit_is_received(server_url, payload):
    with connect(server_url, proxy=None, open_timeout=2, close_timeout=2) as ws:
        authenticate(ws)
        ws.send(payload)
        assert ws.recv(timeout=2) == str(LIMIT)


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("compression", [None, "deflate"])
@pytest.mark.parametrize("kind", ["text", "binary", "fragmented", "utf8"])
def test_oversized_message_closes_with_1009(server_url, authenticated, compression, kind):
    if kind == "text":
        payload = "x" * (LIMIT + 1)
    elif kind == "binary":
        payload = bytes(LIMIT + 1)
    elif kind == "fragmented":
        # 각 프레임은 상한 이내여도 합친 메시지가 넘으면 거절한다.
        payload = [bytes(LIMIT // 2), bytes(LIMIT // 2 + 1)]
    else:
        payload = "é" * (LIMIT // 2) + "x"
    with connect(
        server_url, proxy=None, compression=compression, open_timeout=2, close_timeout=2
    ) as ws:
        if authenticated:
            authenticate(ws)
        with pytest.raises(ConnectionClosed) as closed:
            ws.send(payload)
            ws.recv(timeout=2)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == 1009
