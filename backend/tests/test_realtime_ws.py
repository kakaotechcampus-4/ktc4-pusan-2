"""WebSocket 엔드포인트와 Take 스트림. Deepgram 만 가짜로 바꾸고 나머지는 실제로 돈다.

인증은 실제 JWT 로, 사용자 조회는 테스트 DB 로. 프레임 파싱·offset·무음 채우기·재연결·
번호 연속성·종료 순서가 전부 실제 코드를 거친다.
"""

import asyncio
import json
import struct
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosedError

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime import take_stream
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.dependencies import get_stt_adapter
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


# ── 가짜 Deepgram ─────────────────────────────────────────────────────


@dataclass
class Plan:
    """Deepgram 세션 하나의 대본."""

    replies: list[SttEvent] = field(default_factory=list)
    # 이 개수만큼 오디오를 받은 뒤 연결이 끊긴 것처럼 군다
    die_after: int | None = None


class FakeSttSession:
    """오디오를 받을 때마다 대본에서 하나씩 돌려준다. CloseStream 에 Metadata."""

    def __init__(self, plan: Plan) -> None:
        self.replies = list(plan.replies)
        self.die_after = plan.die_after
        self.audio: list[bytes] = []
        self.controls: list[str] = []
        self.aborted = False
        self.alive = True
        # None 이면 실제로 받은 만큼 보고한다. 값을 넣으면 어긋난 척한다
        self.report_ms: int | None = None
        self._events: asyncio.Queue[SttEvent | None] = asyncio.Queue()

    @property
    def audio_ms(self) -> int:
        return sum(len(a) for a in self.audio) // BYTES_PER_MS

    async def send_audio(self, pcm: bytes) -> None:
        if not self.alive:
            raise ConnectionClosedError(None, None)
        self.audio.append(pcm)
        if self.replies:
            self._events.put_nowait(self.replies.pop(0))
        if self.die_after is not None and len(self.audio) >= self.die_after:
            self.alive = False
            self._events.put_nowait(None)

    async def finalize(self) -> None:
        self.controls.append("Finalize")

    async def close_stream(self) -> None:
        self.controls.append("CloseStream")
        reported = self.audio_ms if self.report_ms is None else self.report_ms
        self._events.put_nowait(Metadata(request_id="fake", duration_ms=reported))
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
    def __init__(self, *plan: Plan, fail_times: int = 0) -> None:
        self.plan = list(plan)
        self.fail_times = fail_times
        self.configs: list[SttConfig] = []
        self.sessions: list[FakeSttSession] = []

    async def connect(self, config: SttConfig) -> FakeSttSession:
        self.configs.append(config)
        if len(self.configs) <= self.fail_times:
            raise SttConnectError("boom")
        index = len(self.sessions)
        session = FakeSttSession(self.plan[index] if index < len(self.plan) else Plan())
        self.sessions.append(session)
        return session

    @property
    def session(self) -> FakeSttSession | None:
        return self.sessions[-1] if self.sessions else None


# ── fixture ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_leftover_streams():
    """스트림은 연결보다 오래 산다. 테스트 사이에 넘어가면 죽은 이벤트 루프의 태스크가 남는다."""
    yield
    for stream in list(take_stream._streams.values()):
        stream.cancel()
    take_stream._streams.clear()
    app.dependency_overrides.pop(get_stt_adapter, None)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(take_stream, "BACKOFF_SEC", (0.01,))
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 2.0)


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


def use(adapter: FakeSttAdapter) -> FakeSttAdapter:
    app.dependency_overrides[get_stt_adapter] = lambda: adapter
    return adapter


# ── helper ────────────────────────────────────────────────────────────


def auth(ws, token: str) -> None:
    ws.send_text(json.dumps({"type": "auth", "token": token}))


def handshake(ws, token: str) -> dict:
    auth(ws, token)
    ready = ws.receive_json()
    assert ready["type"] == "ready", ready
    return ready


def wait_state(ws, state: str) -> dict:
    """그 상태의 stt_status 가 올 때까지 읽는다."""
    while True:
        message = ws.receive_json()
        assert message["type"] == "stt_status", message
        if message["state"] == state:
            return message


def closed_with(ws) -> int:
    """서버가 보낸 close 프레임의 코드. TestClient 는 receive() 에 원시 메시지를 돌려준다."""
    message = ws.receive()
    assert message["type"] == "websocket.close", message
    return message["code"]


def stop(ws) -> dict:
    ws.send_text(json.dumps({"type": "stop"}))
    return wait_state(ws, "closed")


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
        pytest.raises(Exception) as info,
        client.websocket_connect(WS_PATH, headers={"origin": "https://evil.example"}),
    ):
        pass
    assert getattr(info.value, "code", None) == 1008


def test_frontend_origin_is_allowed(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH, headers={"origin": "http://localhost:3000"}) as ws:
        assert handshake(ws, token)["type"] == "ready"


# ── 연결과 상태 ────────────────────────────────────────────────────────


def test_ready_reports_state_before_deepgram_is_up(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        ready = handshake(ws, token)
        # Deepgram 연결을 기다리지 않는다. 붙으면 ok 가 따라온다
        assert ready == {
            "type": "ready",
            "take_id": str(TAKE_ID),
            "stt_session_no": 0,
            "stt_state": "connecting",
        }
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    (config,) = stt.configs
    assert "음" in config.keyterms and "이제" in config.keyterms
    assert config.tag == f"take:{TAKE_ID}"


def test_deepgram_outage_degrades_instead_of_closing(client: TestClient, token: str):
    # 3번 실패하면 degraded 를 알리고 그래도 재시도를 이어간다
    use(FakeSttAdapter(fail_times=3))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "degraded")["stt_session_no"] == 0
        # 연결은 살아 있다 — FE 1단 코치가 계속 돌아야 한다
        assert wait_state(ws, "ok")["stt_session_no"] == 1


# ── 전사 ──────────────────────────────────────────────────────────────


def test_transcripts_are_shifted_to_take_timeline(client: TestClient, token: str):
    # Deepgram 세션 기준 0~100ms 가 Take 기준 5000~5100ms 여야 한다
    adapter = use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 100, "안녕", is_final=False),
                    transcript(0, 200, "안녕하세요", is_final=True),
                    transcript(200, 300, "그래서", is_final=False),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

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
    assert adapter.session is not None


# ── 프레임 처리 ────────────────────────────────────────────────────────


def test_stop_drains_deepgram_then_closes_normally(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 100))
        status = stop(ws)
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
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))  # 0~100
        ws.send_bytes(frame(2, 400))  # 300ms 비었다
        status = stop(ws)

    session = stt.session
    assert session is not None
    assert [len(a) for a in session.audio] == [3200, 300 * BYTES_PER_MS, 3200]
    assert session.audio[1] == bytes(300 * BYTES_PER_MS)
    assert status["silence_ms"] == 300 and status["frames"] == 2


def test_jitter_is_not_a_gap(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 130))  # 30ms 어긋남은 타이머 지터
        status = stop(ws)

    assert stt.session is not None
    assert [len(a) for a in stt.session.audio] == [3200, 3200]
    assert status["silence_ms"] == 0


def test_duplicate_and_out_of_order_frames_are_dropped(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(2, 100))
        ws.send_bytes(frame(1, 0))  # 역행
        ws.send_bytes(frame(2, 100))  # 중복
        ws.send_bytes(frame(3, 200))
        status = stop(ws)

    assert (status["frames"], status["dropped_frames"]) == (2, 2)
    assert stt.session is not None
    assert len(stt.session.audio) == 2


def test_malformed_frame_is_reported_once_and_ignored(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(struct.pack("<II", 1, 0) + b"\x01" * 3201)  # 홀수 바이트
        assert ws.receive_json()["code"] == "BAD_AUDIO_FRAME"
        ws.send_bytes(frame(2, 100))
        status = stop(ws)

    assert status["frames"] == 1
    assert stt.session is not None
    assert [len(a) for a in stt.session.audio] == [3200]


def test_unknown_text_message_is_an_error_not_a_close(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_text(json.dumps({"type": "dance"}))
        assert ws.receive_json()["code"] == "BAD_MESSAGE"
        assert stop(ws)["state"] == "closed"


# ── WS-2 재연결 (Deepgram 이 끊긴다) ───────────────────────────────────


def test_deepgram_session_replacement_keeps_segment_numbering(client: TestClient, token: str):
    """§6-2 주의 2 — 번호가 리셋되면 FE 가 앞부분 전사를 뒷부분으로 덮어쓴다."""
    adapter = use(
        FakeSttAdapter(
            Plan(replies=[transcript(0, 200, "안녕하세요", is_final=True)], die_after=1),
            Plan(replies=[transcript(0, 100, "그래서", is_final=False)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        ws.send_bytes(frame(1, 0))
        first = ws.receive_json()
        assert (first["segment_id"], first["is_final"]) == ("1-1", True)

        # 세션이 죽었다 -> 재접속
        wait_state(ws, "reconnecting")
        assert wait_state(ws, "ok")["stt_session_no"] == 2

        ws.send_bytes(frame(2, 5000))
        second = ws.receive_json()
        status = stop(ws)

    # 세션 번호는 올라가고 세그먼트 번호는 **이어진다** (1 로 리셋되지 않는다)
    assert second["segment_id"] == "2-2"
    # 새 세션의 base 는 그 세션의 첫 프레임 offset. 타임라인은 Take 기준으로 유지된다
    assert second["start_ms"] == 5000
    assert status["stt_session_no"] == 2
    assert len(adapter.sessions) == 2


def test_audio_queued_while_deepgram_is_down_is_sent_after_reconnect(
    client: TestClient, token: str
):
    adapter = use(FakeSttAdapter(fail_times=1))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        # 첫 연결이 실패한 동안 보낸 오디오도 큐에 남는다
        ws.send_bytes(frame(1, 0))
        wait_state(ws, "ok")
        ws.send_bytes(frame(2, 100))
        stop(ws)

    assert adapter.session is not None
    assert [len(a) for a in adapter.session.audio] == [3200, 3200]


# ── WS-1 재연결 (FE 가 끊긴다) ─────────────────────────────────────────


def test_reconnect_within_grace_keeps_the_same_deepgram_session(client: TestClient, token: str):
    adapter = use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 200, "안녕하세요", is_final=True),
                    transcript(200, 300, "그래서", is_final=False),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        assert ws.receive_json()["segment_id"] == "1-1"

    # 탭이 끊겼다. grace 안에 다시 붙는다
    with client.websocket_connect(WS_PATH) as ws:
        ready = handshake(ws, token)
        assert (ready["stt_session_no"], ready["stt_state"]) == (1, "ok")
        ws.send_bytes(frame(2, 100))
        again = ws.receive_json()
        status = stop(ws)

    assert len(adapter.sessions) == 1, "Deepgram 세션은 그대로여야 한다"
    assert again["segment_id"] == "1-2"
    assert status["frames"] == 2, "프레임 수도 Take 누적이다"


def test_grace_expiry_closes_the_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))

    deadline = time.monotonic() + 3.0
    while take_stream.active_count() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert take_stream.active_count() == 0
    assert adapter.session is not None
    # 그냥 버리지 않고 CloseStream 으로 마지막 전사까지 받아 둔다
    assert adapter.session.controls == ["CloseStream"]


def test_second_tab_takes_over_and_evicts_the_first(client: TestClient, token: str):
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as first:
        handshake(first, token)
        wait_state(first, "ok")

        with client.websocket_connect(WS_PATH) as second:
            assert handshake(second, token)["stt_session_no"] == 1

            assert first.receive_json()["code"] == "TAKE_TAKEN_OVER"
            assert closed_with(first) == 1008

            second.send_bytes(frame(1, 0))
            stop(second)

    assert len(adapter.sessions) == 1


# ── Metadata 대조 ─────────────────────────────────────────────────────


def test_duration_mismatch_is_logged(
    client: TestClient, stt: FakeSttAdapter, token: str, caplog: pytest.LogCaptureFixture
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        # 우리는 100ms 를 보냈는데 Deepgram 은 40ms 만 받았다고 한다 = 그만큼 유실
        assert stt.session is not None
        stt.session.report_ms = 40
        with caplog.at_level("WARNING"):
            stop(ws)

    assert "sent_ms=100 duration_ms=40 drift_ms=-60" in caplog.text
