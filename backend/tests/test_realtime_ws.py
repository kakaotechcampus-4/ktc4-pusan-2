"""WebSocket 엔드포인트. Deepgram 은 가짜 세션으로 바꿔 끼우고 나머지는 실제로 돈다.

인증은 실제 JWT 로, 사용자 조회는 테스트 DB 로. 프레임 파싱·offset·무음 채우기·종료 순서가
전부 실제 코드를 거친다.
"""

import asyncio
import json
import struct
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.dependencies import get_stt_adapter
from pitch_coach_backend.realtime.event_ingestion import BYTES_PER_MS
from pitch_coach_backend.realtime.stt_adapter import (
    Metadata,
    SttConfig,
    SttConnectError,
    SttEvent,
    Transcript,
    Word,
)

TAKE_ID = uuid.uuid7()
WS_PATH = f"/api/ws/takes/{TAKE_ID}"


def frame(seq: int, offset_ms: int, duration_ms: int = 100, fill: int = 1) -> bytes:
    return struct.pack("<II", seq, offset_ms) + bytes([fill]) * (duration_ms * BYTES_PER_MS)


def transcript(start_ms: int, end_ms: int, text: str, *, is_final: bool) -> Transcript:
    return Transcript(
        start_ms=start_ms,
        end_ms=end_ms,
        is_final=is_final,
        speech_final=is_final,
        from_finalize=False,
        transcript=text,
        confidence=0.9,
        words=(Word(text, text, start_ms, end_ms, 0.9),),
    )


class FakeSttSession:
    """오디오를 받을 때마다 replies 에서 하나씩 꺼내 돌려준다. CloseStream 에 Metadata."""

    def __init__(self, replies: list[SttEvent]) -> None:
        self.replies = list(replies)
        self.audio: list[bytes] = []
        self.controls: list[str] = []
        self.aborted = False
        self._events: asyncio.Queue[SttEvent | None] = asyncio.Queue()

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)
        if self.replies:
            self._events.put_nowait(self.replies.pop(0))

    async def finalize(self) -> None:
        self.controls.append("Finalize")

    async def close_stream(self) -> None:
        self.controls.append("CloseStream")
        self._events.put_nowait(Metadata(request_id="fake", duration_ms=0))
        self._events.put_nowait(None)

    async def abort(self) -> None:
        self.aborted = True
        self._events.put_nowait(None)

    async def keepalive_loop(self) -> None:
        await asyncio.Event().wait()

    async def __aiter__(self) -> AsyncIterator[SttEvent]:
        while (event := await self._events.get()) is not None:
            yield event


class FakeSttAdapter:
    def __init__(self, replies: list[SttEvent] | None = None, *, fail: bool = False) -> None:
        self.replies = replies or []
        self.fail = fail
        self.configs: list[SttConfig] = []
        self.session: FakeSttSession | None = None

    async def connect(self, config: SttConfig) -> FakeSttSession:
        self.configs.append(config)
        if self.fail:
            raise SttConnectError("boom")
        self.session = FakeSttSession(self.replies)
        return self.session


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(email="ws@example.com", name="WS")
    db_session.add(user)
    # 서비스가 인증 뒤 rollback() 으로 커넥션을 돌려준다. 커밋(=savepoint 해제) 해 둬야 살아남는다
    db_session.commit()
    return user


@pytest.fixture
def token(user: User) -> str:
    return create_access_token(user.id)


@pytest.fixture
def stt(client: TestClient) -> FakeSttAdapter:
    adapter = FakeSttAdapter()
    app.dependency_overrides[get_stt_adapter] = lambda: adapter
    return adapter


def auth(ws, token: str) -> None:
    ws.send_text(json.dumps({"type": "auth", "token": token}))


def closed_with(ws) -> int:
    """서버가 보낸 close 프레임의 코드. TestClient 는 receive() 에 원시 메시지를 돌려준다."""
    message = ws.receive()
    assert message["type"] == "websocket.close", message
    return message["code"]


# ── 인증 ──────────────────────────────────────────────────────────────


def test_bad_token_is_rejected_with_1008(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, "garbage")
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "인증 전에 Deepgram 에 붙지 않는다"


def test_unknown_user_is_rejected(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, create_access_token(uuid.uuid7()))
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008


def test_audio_before_auth_is_rejected(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_bytes(frame(1, 0))
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008


def test_foreign_origin_is_closed_before_accept(client: TestClient, stt: FakeSttAdapter):
    with (
        pytest.raises(WebSocketDisconnect) as info,
        client.websocket_connect(WS_PATH, headers={"origin": "https://evil.example"}),
    ):
        pass
    assert info.value.code == 1008


def test_frontend_origin_is_allowed(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH, headers={"origin": "http://localhost:3000"}) as ws:
        auth(ws, token)
        assert ws.receive_json()["type"] == "ready"


# ── 스트리밍 ──────────────────────────────────────────────────────────


def test_ready_carries_take_and_session(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ready = ws.receive_json()

    assert ready == {"type": "ready", "take_id": str(TAKE_ID), "stt_session_no": 1}
    (config,) = stt.configs
    assert "음" in config.keyterms and "이제" in config.keyterms
    assert config.tag == f"take:{TAKE_ID}"


def test_transcripts_are_shifted_to_take_timeline(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    # Deepgram 세션 기준 0~100ms 가 Take 기준 5000~5100ms 여야 한다
    stt.replies = [
        transcript(0, 100, "안녕", is_final=False),
        transcript(0, 200, "안녕하세요", is_final=True),
        transcript(200, 300, "그래서", is_final=False),
    ]
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()

        ws.send_bytes(frame(1, 5000))
        interim = ws.receive_json()
        ws.send_bytes(frame(2, 5100))
        final = ws.receive_json()
        ws.send_bytes(frame(3, 5200))
        next_interim = ws.receive_json()

    assert interim["type"] == "transcript"
    assert (interim["segment_id"], interim["is_final"]) == ("1-1", False)
    assert (interim["start_ms"], interim["end_ms"]) == (5000, 5100)
    assert interim["words"][0]["start_ms"] == 5000

    assert (final["segment_id"], final["is_final"], final["text"]) == ("1-1", True, "안녕하세요")
    assert (final["start_ms"], final["end_ms"]) == (5000, 5200)

    # final 뒤의 interim 은 새 구간
    assert (next_interim["segment_id"], next_interim["start_ms"]) == ("1-2", 5200)


def test_stop_drains_deepgram_then_closes_normally(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 100))
        ws.send_text(json.dumps({"type": "stop"}))

        status = ws.receive_json()
        assert closed_with(ws) == 1000

    assert status == {
        "type": "stt_status",
        "state": "closed",
        "stt_session_no": 1,
        "frames": 2,
        "dropped_frames": 0,
        "silence_ms": 0,
        "lost_ms": 0,
    }
    session = stt.session
    assert session is not None
    assert session.controls == ["CloseStream"]
    assert [len(a) for a in session.audio] == [3200, 3200]


def test_gap_is_filled_with_silence(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(frame(1, 0))  # 0~100
        ws.send_bytes(frame(2, 400))  # 300ms 비었다
        ws.send_text(json.dumps({"type": "stop"}))
        status = ws.receive_json()

    session = stt.session
    assert session is not None
    assert [len(a) for a in session.audio] == [3200, 300 * BYTES_PER_MS, 3200]
    assert session.audio[1] == bytes(300 * BYTES_PER_MS)
    assert status["silence_ms"] == 300 and status["frames"] == 2


def test_jitter_is_not_a_gap(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 130))  # 30ms 어긋남은 타이머 지터
        ws.send_text(json.dumps({"type": "stop"}))
        status = ws.receive_json()

    assert [len(a) for a in stt.session.audio] == [3200, 3200]
    assert status["silence_ms"] == 0


def test_duplicate_and_out_of_order_frames_are_dropped(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(frame(2, 100))
        ws.send_bytes(frame(1, 0))  # 역행
        ws.send_bytes(frame(2, 100))  # 중복
        ws.send_bytes(frame(3, 200))
        ws.send_text(json.dumps({"type": "stop"}))
        status = ws.receive_json()

    assert (status["frames"], status["dropped_frames"]) == (2, 2)
    assert len(stt.session.audio) == 2


def test_malformed_frame_is_reported_once_and_ignored(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(struct.pack("<II", 1, 0) + b"\x01" * 3201)  # 홀수 바이트
        assert ws.receive_json()["code"] == "BAD_AUDIO_FRAME"
        ws.send_bytes(frame(2, 100))
        ws.send_text(json.dumps({"type": "stop"}))
        status = ws.receive_json()

    assert status["frames"] == 1
    assert [len(a) for a in stt.session.audio] == [3200]


def test_unknown_text_message_is_an_error_not_a_close(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_text(json.dumps({"type": "dance"}))
        assert ws.receive_json()["code"] == "BAD_MESSAGE"
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["state"] == "closed"


def test_stt_connect_failure_closes_with_1011(client: TestClient, token: str):
    app.dependency_overrides[get_stt_adapter] = lambda: FakeSttAdapter(fail=True)
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "STT_UNAVAILABLE"
        assert closed_with(ws) == 1011


def test_client_disconnect_aborts_deepgram(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ws.receive_json()
        ws.send_bytes(frame(1, 0))
    # with 블록을 나가며 FE 가 끊었다
    assert stt.session is not None
    assert stt.session.aborted
