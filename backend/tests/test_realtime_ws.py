"""WebSocket 엔드포인트와 Take 스트림. Deepgram 과 전사 저장소만 가짜로 바꾸고 나머지는 실제로 돈다.

인증은 실제 JWT 로, 사용자·Take 조회는 테스트 DB 로. 프레임 파싱·offset·무음 채우기·재연결·
번호 연속성·종료 순서가 전부 실제 코드를 거친다. 저장소는 기본이 가짜(메모리) 이고,
실제 DB 저장은 `DbTranscriptStore` 를 테스트 세션에 묶어 따로 확인한다.
"""

import asyncio
import contextlib
import json
import struct
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosedError

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO
from pitch_coach_backend.module.take.entity import Take, TakeTranscriptSegment
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime import service, take_stream
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.dependencies import get_stt_adapter, get_transcript_store
from pitch_coach_backend.realtime.dto import ErrorMessage, TranscriptMessage, WsErrorCode
from pitch_coach_backend.realtime.stt_adapter import (
    Metadata,
    SttConfig,
    SttConnectError,
    SttEvent,
    Transcript,
    Word,
)
from pitch_coach_backend.realtime.take_stream import TakeStream
from pitch_coach_backend.realtime.transcript_store import DbTranscriptStore

TAKE_ID = uuid.uuid7()
WS_PATH = f"/api/ws/takes/{TAKE_ID}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    # CloseStream 에 Metadata 도 종료도 돌려주지 않는다 (drain 타임아웃 재현)
    swallow_close: bool = False
    # abort() 가 이만큼 걸린다 (cancel() 이 정리를 기다리는지 재현하는 용)
    abort_delay: float = 0.0
    # send_audio 가 예상 못 한 예외를 던진다 (어댑터 버그 재현)
    explode_on_send: bool = False


class FakeSttSession:
    """오디오를 받을 때마다 대본에서 하나씩 돌려준다. CloseStream 에 Metadata."""

    def __init__(self, plan: Plan) -> None:
        self.replies = list(plan.replies)
        self.die_after = plan.die_after
        self.swallow_close = plan.swallow_close
        self.abort_delay = plan.abort_delay
        self.explode_on_send = plan.explode_on_send
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
        if self.explode_on_send:
            raise ValueError("adapter bug")
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
        if self.swallow_close:
            return
        reported = self.audio_ms if self.report_ms is None else self.report_ms
        self._events.put_nowait(Metadata(request_id="fake", duration_ms=reported))
        self._events.put_nowait(None)

    async def abort(self) -> None:
        if self.abort_delay:
            await asyncio.sleep(self.abort_delay)
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


class FakeTranscriptStore:
    """final 을 메모리에 모은다. cursor 는 "이미 저장된 마지막 번호" 를 흉내 낸다."""

    def __init__(self, *, cursor: tuple[int, int] = (0, 0), fail: bool = False) -> None:
        self.segments: list[TranscriptSegmentCreateDTO] = []
        self.take_ids: list[uuid.UUID] = []
        self._cursor = cursor
        self.fail = fail

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        return self._cursor

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await asyncio.sleep(0)  # 실제 저장은 threadpool 왕복이라 한 번은 양보한다
        if self.fail:
            raise RuntimeError("db down")
        self.take_ids.append(take_id)
        self.segments.append(segment)


# ── fixture ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_leftover_streams():
    """스트림은 연결보다 오래 산다. 테스트 사이에 넘어가면 죽은 이벤트 루프의 태스크가 남는다."""
    yield
    for stream in list(take_stream._streams.values()):
        stream.cancel()
    take_stream._streams.clear()
    take_stream._attach_locks.clear()
    app.dependency_overrides.pop(get_stt_adapter, None)
    app.dependency_overrides.pop(get_transcript_store, None)


@pytest.fixture(autouse=True)
def store() -> FakeTranscriptStore:
    """기본 저장소는 가짜다. 실제 DbTranscriptStore 는 SessionLocal 로 새 커넥션을 열어서
    테스트 트랜잭션 안의(아직 커밋 안 된) Take 행을 못 보고 FK 위반이 난다."""
    fake = FakeTranscriptStore()
    app.dependency_overrides[get_transcript_store] = lambda: fake
    return fake


def use_store(fake: FakeTranscriptStore) -> FakeTranscriptStore:
    app.dependency_overrides[get_transcript_store] = lambda: fake
    return fake


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
def other_token(db_session: Session) -> str:
    other = User(email="other@example.com", name="다른 사람")
    db_session.add(other)
    db_session.commit()
    return create_access_token(other.id)


@pytest.fixture
def take(db_session: Session, user: User) -> Take:
    """user 소유의 RUNNING Take. id 는 모듈 상수 TAKE_ID 라 WS_PATH 가 그대로 맞는다.

    각 테스트가 롤백되는 트랜잭션 안에서 돌기 때문에 같은 id 를 매번 넣어도 안 겹친다."""
    return make_take(db_session, user.id, take_id=TAKE_ID)


def make_take(
    db_session: Session,
    user_id: uuid.UUID,
    *,
    take_id: uuid.UUID | None = None,
    status: str = "RUNNING",
) -> Take:
    pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_url="deck.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, file_url="script.txt")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="FULL",
        status=status,
    )
    if take_id is not None:
        take.id = take_id
    db_session.add(take)
    # 서비스가 인가 뒤 rollback() 으로 커넥션을 돌려준다. 커밋(=savepoint 해제) 해 둬야 살아남는다
    db_session.commit()
    return take


@pytest.fixture
def token(user: User, take: Take) -> str:
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


# ── 소유권·정리 (코드 리뷰 지적) ───────────────────────────────────────


def test_other_user_cannot_take_over_the_stream(
    client: TestClient, stt: FakeSttAdapter, token: str, other_token: str
):
    """남의 Take 는 DB 검사에서 막힌다. 존재 여부를 흘리지 않도록 "없음" 과 같은 코드다."""
    with client.websocket_connect(WS_PATH) as mine:
        handshake(mine, token)
        wait_state(mine, "ok")

        with client.websocket_connect(WS_PATH) as theirs:
            auth(theirs, other_token)
            assert theirs.receive_json()["code"] == "TAKE_NOT_FOUND"
            assert closed_with(theirs) == 1008

        # 내 연결은 그대로다 — 쫓겨나지 않았다
        mine.send_bytes(frame(1, 0))
        assert stop(mine)["frames"] == 1

    assert len(stt.sessions) == 1


def test_stop_timeout_cancels_the_stream_instead_of_leaking_it(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """drain 이 실패해도 레지스트리에서만 사라지고 태스크가 계속 도는 일이 없어야 한다."""
    monkeypatch.setattr(service, "STOP_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 5.0)
    adapter = use(FakeSttAdapter(Plan(swallow_close=True)))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        assert stop(ws)["state"] == "closed"
        assert closed_with(ws) == 1000

    assert take_stream.active_count() == 0
    assert adapter.session is not None
    # 태스크를 끊었으면 Deepgram 소켓도 닫혀야 한다 (shield 안의 abort)
    assert adapter.session.aborted


def test_reconnect_while_stopping_gets_a_fresh_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """정리 중인 스트림에 붙으면 소비되지 않는 큐에 오디오가 쌓인다."""
    monkeypatch.setattr(take_stream, "HANDOVER_TIMEOUT_SEC", 2.0)
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.3)
    adapter = use(FakeSttAdapter(Plan(swallow_close=True), Plan()))

    first = client.websocket_connect(WS_PATH)
    ws1 = first.__enter__()
    handshake(ws1, token)
    wait_state(ws1, "ok")
    ws1.send_text(json.dumps({"type": "stop"}))  # 응답을 기다리지 않는다

    with client.websocket_connect(WS_PATH) as ws2:
        ready = handshake(ws2, token)
        wait_state(ws2, "ok")
        ws2.send_bytes(frame(1, 0))
        status = stop(ws2)

    with contextlib.suppress(Exception):
        first.__exit__(None, None, None)

    # 정리 중인 스트림을 재사용하지 않고 새로 만든다
    assert ready["stt_session_no"] in (0, 1)
    assert len(adapter.sessions) == 2
    assert status["frames"] == 1, "새 스트림의 통계는 처음부터 센다"


def test_long_gap_rotates_the_session_and_keeps_take_timeline(client: TestClient, token: str):
    """무음으로 메울 수 없는 갭 뒤에도 전사 시각이 Take 기준이어야 한다."""
    adapter = use(
        FakeSttAdapter(
            # 세션 1 에 답을 둘 준다. 교체가 안 되면 두 번째 프레임이 여기로 가서
            # Deepgram 시각 5,100ms(= 100 + 무음 5,000) 로 답이 오고 아래 assert 가 잡는다.
            # 답이 하나뿐이면 교체 실패 시 전사가 안 와서 테스트가 실패 대신 멈춘다
            Plan(
                replies=[
                    transcript(0, 100, "앞", is_final=True),
                    transcript(5_100, 5_200, "뒤", is_final=True),
                ]
            ),
            Plan(replies=[transcript(0, 100, "뒤", is_final=True)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        ws.send_bytes(frame(1, 0))
        first = ws.receive_json()
        # 10초 갭 — MAX_SILENCE_FILL_MS(5초) 를 넘는다
        ws.send_bytes(frame(2, 10_100))
        second = ws.receive_json()
        status = stop(ws)

    assert first["start_ms"] == 0
    # 예전 동작: 5초만 무음으로 채우고 5,100ms 로 보고했다 (Take 시계와 5초 어긋남)
    assert second["start_ms"] == 10_100
    assert second["segment_id"] == "2-2", "세션은 갈리고 세그먼트 번호는 이어진다"
    assert len(adapter.sessions) == 2
    # 못 받은 오디오는 유실로 기록하되 무음으로 밀어 넣지는 않는다
    assert status["lost_ms"] == 10_000
    assert status["silence_ms"] == 0
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]


def test_finals_that_arrive_while_detached_are_resent_on_reconnect(client: TestClient, token: str):
    """grace 동안 온 final 을 버리면 저장이 없는 지금은 그 발화가 어디에도 안 남는다."""
    adapter = use(
        FakeSttAdapter(Plan(replies=[transcript(0, 200, "놓친 말", is_final=True)]), fail_times=1)
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        # Deepgram 이 아직 안 붙었다. 프레임은 큐에 쌓인다
        ws.send_bytes(frame(1, 0))

    # 연결이 끊긴 사이에 Deepgram 이 붙고 final 이 도착한다
    deadline = time.monotonic() + 3.0
    while not adapter.sessions and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        missed = ws.receive_json()
        stop(ws)

    assert missed["type"] == "transcript"
    assert (missed["text"], missed["is_final"]) == ("놓친 말", True)


def test_error_code_must_be_a_known_value():
    """code 는 FE 분기 키다. 오타를 런타임에서 막는다 (이 프로젝트에 정적 타입 검사기는 없다)."""
    assert (
        json.loads(ErrorMessage(code=WsErrorCode.BAD_MESSAGE, message="x").model_dump_json())[
            "code"
        ]
        == "BAD_MESSAGE"
    )
    with pytest.raises(ValidationError):
        ErrorMessage(code="TYPO_CODE", message="x")


# ── 최소 WebSocket 더블 ──────────────────────────────────────────────


class FakeWebSocket:
    """RealtimeSession/TakeStream 이 쓰는 최소 인터페이스만 흉내 낸다.

    TestClient 없이 run() 을 직접 몰거나, attach() 만 따로 확인할 때 쓴다.
    """

    def __init__(
        self,
        inbound: list[dict] | None = None,
        *,
        fail_send_after: int | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        self._inbound = list(inbound or [])
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None
        # 이 번째(1-base) send_text 호출부터 실패시킨다. None 이면 항상 성공
        self.fail_send_after = fail_send_after
        self._send_count = 0

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict:
        if self._inbound:
            return self._inbound.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        # 실제 WebSocket 전송은 항상 I/O 라 이벤트 루프에 제어를 한 번 돌려준다.
        # 여기서 진짜로 양보하지 않으면 asyncio.gather 로 묶어도 두 호출이 교차 실행되지
        # 않아(둘 다 끝까지 한 번에 실행됨) 동시 호출 경합을 재현하는 테스트가 무의미해진다
        await asyncio.sleep(0)
        self._send_count += 1
        if self.fail_send_after is not None and self._send_count >= self.fail_send_after:
            # Starlette 는 끊긴 상대에게 보내면 WebSocketDisconnect(1006) 를 던진다
            raise WebSocketDisconnect(code=1006)
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def transcript_message(segment_id: str, text: str) -> TranscriptMessage:
    return TranscriptMessage(
        segment_id=segment_id,
        is_final=True,
        speech_final=True,
        start_ms=0,
        end_ms=100,
        text=text,
        confidence=0.9,
        words=[],
    )


async def _wait_until_ok(stream: TakeStream, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while stream.state != "ok" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert stream.state == "ok", f"연결이 ok 상태가 되지 않았다 (state={stream.state})"


# ── P1 인증 후 DB 커넥션을 다시 여는 문제 ───────────────────────────────


def test_authenticate_does_not_touch_user_after_rollback(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """rollback() 은 세션의 객체를 전부 expire 시킨다. 그 뒤 user.id 를 읽으면 SQLAlchemy
    가 값을 다시 읽으려고 커넥션을 풀에서 또 꺼낸다 — 방금 돌려준 의미가 없어진다.
    이미 알고 있는 user_id(요청 값) 를 그대로 쓰면 이 문제 자체가 없다."""
    tripwire = {"rolled_back": False, "id_read_after_rollback": False}

    class TripwireUser:
        def __init__(self, real_id: uuid.UUID) -> None:
            self._id = real_id

        @property
        def id(self) -> uuid.UUID:
            if tripwire["rolled_back"]:
                tripwire["id_read_after_rollback"] = True
            return self._id

    monkeypatch.setattr(service.user_service, "find", lambda db, uid: TripwireUser(user.id))
    original_rollback = db_session.rollback

    def rollback_and_arm() -> None:
        original_rollback()
        tripwire["rolled_back"] = True

    monkeypatch.setattr(db_session, "rollback", rollback_and_arm)

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert tripwire["rolled_back"] is True
    assert tripwire["id_read_after_rollback"] is False, (
        "user.id 를 rollback 이후에 읽었다 — 커넥션을 다시 연다"
    )


# ── ready 전송 실패 시 detach() 가 안 불리는 문제 ───────────────────────


@pytest.mark.anyio
async def test_ready_send_failure_still_detaches_from_the_stream(
    db_session: Session, user: User, token: str
):
    """ready 전송·resend_missed·pump_client 가 하나의 try/finally 로 묶여야 한다.
    그중 아무거나 먼저 실패해도 detach() 가 불려야 스트림에 grace 타이머가 걸린다.
    안 그러면 죽은 self.ws 가 "현재 클라이언트" 로 영원히 남아 Deepgram 세션이
    다시는 정리되지 않는다. 그리고 run() 은 이 예외를 밖으로 내보내지 않는다 — 클라이언트가
    사라진 건 오류가 아니라 흔한 종료라, 새면 uvicorn 이 연결마다 트레이스를 남긴다."""
    adapter = FakeSttAdapter()
    ws = FakeWebSocket(
        inbound=[
            {"type": "websocket.receive", "text": json.dumps({"type": "auth", "token": token})}
        ],
        fail_send_after=1,  # 첫 send_text = ready 메시지에서 터진다
    )
    realtime = service.RealtimeSession(
        ws,
        take_id=TAKE_ID,
        db=db_session,
        stt_adapter=adapter,
        transcript_store=FakeTranscriptStore(),
    )

    await realtime.run()  # 예외가 새지 않는다

    stream = take_stream._streams[TAKE_ID]
    assert stream._client is None, "detach() 가 안 불리면 죽은 ws 가 현재 클라이언트로 남는다"
    stream.cancel()


# ── final 재전송이 실패하면 나머지도 같이 사라지는 문제 ─────────────────


@pytest.mark.anyio
async def test_resend_missed_keeps_unsent_finals_when_send_fails_partway():
    """한꺼번에 비우고 보내면, 두 번째 전송이 실패했을 때 아직 못 보낸 세 번째 이후가
    이미 비워진 리스트와 함께 사라진다. 성공한 만큼만 지워야 다음 재연결 때
    나머지를 다시 시도할 수 있다."""
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream._missed_finals = [
        transcript_message("1-1", "첫"),
        transcript_message("1-2", "둘"),
        transcript_message("1-3", "셋"),
    ]
    fake_ws = FakeWebSocket(fail_send_after=2)  # 두 번째 전송에서 끊긴다
    await stream.attach(fake_ws)

    await stream.resend_missed()

    assert [json.loads(t)["text"] for t in fake_ws.sent] == ["첫"]
    assert [m.text for m in stream._missed_finals] == ["둘", "셋"], (
        "실패한 지점부터는 다음 재연결을 위해 남아 있어야 한다"
    )


# ── 종료 중 동시 재연결이 스트림을 두 번 만드는 문제 ────────────────────


@pytest.mark.anyio
async def test_concurrent_attach_during_handover_does_not_duplicate_stream(
    monkeypatch: pytest.MonkeyPatch,
):
    """확인(get) -> 생성(TakeStream) 이 원자적이지 않으면, 정리 중인 스트림에
    두 연결이 동시에 오는 순간 둘 다 "없다" 를 보고 각자 새 스트림·Deepgram 세션을
    만든다. 하나는 레지스트리에서 밀려나 고아로 남는다."""
    monkeypatch.setattr(take_stream, "HANDOVER_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan(), Plan())

    first = await take_stream.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    await _wait_until_ok(first)
    first.request_stop()  # swallow_close 라서 스스로는 절대 안 끝난다 -> is_stopping 만 유지된다

    second, third = await asyncio.gather(
        take_stream.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        ),
        take_stream.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        ),
    )

    assert second is third, "같은 take_id 로 겹친 재연결은 같은 스트림으로 수렴해야 한다"
    assert take_stream.active_count() == 1
    # Deepgram 연결은 처음(1) + 핸드오버 뒤 새로 한 번(1) = 2 여야 한다. 락이 없으면 3 이 된다
    assert len(adapter.sessions) == 2


# ── cancel() 이 요청을 완료로 착각하는 문제 ─────────────────────────────


@pytest.mark.anyio
async def test_cancel_waits_for_real_deepgram_cleanup_before_reporting_closed():
    """cancel() 은 취소 '요청' 일 뿐이다. task.cancel() 은 다음 await 지점에서
    CancelledError 를 던지도록 예약할 뿐 그 자리에서 정리가 끝나는 게 아니다.
    실제 정리(Deepgram 소켓을 shield 안에서 닫는 것)가 끝나야 closed 여야 한다."""
    adapter = FakeSttAdapter(Plan(abort_delay=0.15))
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()
    assert not stream.is_finished, "cancel() 직후인데 벌써 닫혔다고 보고했다"
    await asyncio.sleep(0.05)  # abort_delay(0.15) 보다 짧다 — 아직 안 끝났어야 한다
    assert not stream.is_finished, "cancel() 이 실제 정리(abort)를 기다리지 않았다"

    closed = await stream.wait_closed(timeout=1.0)
    assert closed is True
    assert adapter.sessions[0].aborted is True


# ── 긴 갭 직후 stop 이 남은 오디오를 버리는 문제 ────────────────────────


@pytest.mark.anyio
async def test_stop_immediately_after_long_gap_still_flushes_carried_audio():
    """세션 회전(_ROTATE)과 stop 이 겹치면, 회전 직전에 넘겨받은 캐리 프레임과 그 뒤
    큐에 남은 stop 신호가 새 세션을 하나 더 열어서라도 처리돼야 한다."""
    adapter = FakeSttAdapter(Plan(), Plan())
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.push(frame(1, 0))
    # push()·request_stop() 은 둘 다 동기(non-async) 라 이 세 줄 사이에는 백그라운드
    # 태스크가 끼어들 틈이 없다 — "회전을 부르는 프레임과 stop 이 동시에 온다" 는
    # 가장 흔한 경합 모양을 그대로 재현한다
    stream.push(frame(2, 10_100))  # 10초 갭 -> 캐리로 넘어가며 세션 회전을 요구한다
    stream.request_stop()

    closed = await stream.wait_closed(timeout=2.0)
    assert closed is True
    # 회전이 필요했으니 Deepgram 세션이 두 번 열려야 한다. stop 을 먼저 보고 끝내면 1 뿐이다
    assert len(adapter.sessions) == 2
    # 회전으로 넘어간 캐리 프레임(3,200 bytes)이 새 세션에 실제로 전달됐다
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]


# ── 핸드오버 타임아웃 시 옛 스트림이 정리 안 되는 문제 ──────────────────


@pytest.mark.anyio
async def test_handover_timeout_cancels_the_old_stream_instead_of_orphaning_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """DRAIN_TIMEOUT_SEC(정상 drain 상한, 10s) 이 HANDOVER_TIMEOUT_SEC(핸드오버 유예, 3s)
    보다 길어서, 옛 스트림이 아직 정상적으로 정리되는 중이어도 핸드오버는 먼저 포기한다.
    그때 그냥 forget() 만 하면 옛 스트림은 레지스트리에서만 사라질 뿐 자기 페이스대로
    계속 돈다 — Deepgram 연결이 안 끊긴다. cancel() 로 강제로 끊어야 한다."""
    monkeypatch.setattr(take_stream, "HANDOVER_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    # swallow_close: CloseStream 을 보내도 스스로는 절대 안 끝난다 (느린 정상 drain 재현)
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan())

    old_stream = await take_stream.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    await _wait_until_ok(old_stream)
    old_stream.request_stop()  # 정상적으로 정리를 시작했다 (아직 안 끝났을 뿐)

    new_stream = await take_stream.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )

    assert new_stream is not old_stream
    # 옛 스트림이 실제로 끊겼는지 — cancel() 이 안 불리면 이 대기가 계속 False 로 남는다
    assert await old_stream.wait_closed(timeout=1.0) is True, (
        "핸드오버 타임아웃 뒤에도 옛 스트림이 스스로 안 끝나면 강제로 끊어야 한다"
    )
    assert adapter.sessions[0].aborted is True


# ── final 재전송이 겹치면 IndexError 나는 문제 ──────────────────────────


@pytest.mark.anyio
async def test_concurrent_resend_missed_does_not_race_on_the_shared_list():
    """탭 두 개가 거의 동시에 붙으면 서로 다른 RealtimeSession 이 resend_missed() 를
    동시에 부를 수 있다. 락 없이 같은 리스트를 각자 확인-후-pop 하면, 한쪽이 이미
    비운 자리를 다른 쪽이 또 pop 하다가 IndexError 가 난다."""
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream._missed_finals = [
        transcript_message("1-1", "첫"),
        transcript_message("1-2", "둘"),
    ]
    ws_a = FakeWebSocket()
    await stream.attach(ws_a)

    # 두 호출이 서로 await 지점에서 번갈아 실행되도록(진짜 동시 호출처럼) gather 로 묶는다.
    # IndexError 가 나면 gather 자체가 그 예외로 실패한다
    await asyncio.gather(stream.resend_missed(), stream.resend_missed())

    assert stream._missed_finals == []
    # 두 메시지 모두 (중복 없이) 정확히 한 번씩만 나갔다
    sent_texts = [json.loads(t)["text"] for t in ws_a.sent]
    assert sent_texts == ["첫", "둘"]


# ── 태스크가 한 번도 실행되기 전에 취소되면 종료가 기록 안 되는 문제 ────


@pytest.mark.anyio
async def test_cancel_before_task_ever_runs_still_reports_closed():
    """asyncio.create_task() 직후 바로 cancel() 하면(태스크가 단 한 번도 스텝되지
    않은 채로), 코루틴 본문이 아예 실행되지 않아 _run() 의 finally 조차 안 돈다.
    그러면 _finish() 를 아무도 안 불러서 wait_closed() 가 영원히 False 만 준다."""
    adapter = FakeSttAdapter(Plan())
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    stream.cancel()  # start() 직후, 태스크가 단 한 번도 스텝되지 않은 채로 취소한다

    closed = await stream.wait_closed(timeout=1.0)
    assert closed is True
    assert stream.is_finished is True
    assert take_stream.active_count() == 0


# ── Take 연결: 존재·소유·상태 검사 ──────────────────────────────────────


def test_unknown_take_is_rejected_before_deepgram(
    client: TestClient, stt: FakeSttAdapter, user: User
):
    with client.websocket_connect(f"/api/ws/takes/{uuid.uuid7()}") as ws:
        auth(ws, create_access_token(user.id))
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "Take 검사에 걸리면 Deepgram 에 붙지 않는다"
    assert take_stream.active_count() == 0


def test_ended_take_is_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """끝난 연습에 전사를 더 붙이면 리포트가 오염된다. FE 는 이 코드로 재연결하지 않는다."""
    ended = make_take(db_session, user.id, status="COMPLETED")
    with client.websocket_connect(f"/api/ws/takes/{ended.id}") as ws:
        auth(ws, create_access_token(user.id))
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert stt.configs == []


def test_someone_elses_take_is_rejected_even_without_a_live_stream(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, other_token: str
):
    """소유권 검사는 레지스트리(owner_id)가 아니라 DB(takes JOIN pitches.user_id) 가 한다.
    스트림이 하나도 없어도 남의 Take 는 열리지 않고, "없는 Take" 와 같은 코드로 답한다."""
    # TAKE_ID 는 `take` fixture(=token 의존) 가 만드는데 여기선 그걸 안 쓰고 다른 사람 걸 만든다
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, other_token)
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "Deepgram 에 붙지 않는다"
    assert take_stream.active_count() == 0, "스트림도 만들지 않는다"


def test_reconnect_is_authorized_again_even_while_the_stream_is_alive(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, token: str, take: Take
):
    """검사는 연결마다 한다. grace 동안 스트림이 살아 있어도 그 사이 Take 가 끝났으면
    (예: complete 처리) 재연결은 거절된다 — 레지스트리에 있다고 통과시키지 않는다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
    assert take_stream.active_count() == 1, "grace 중이라 스트림은 살아 있다"

    take.status = "COMPLETED"
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(stt.sessions) == 1, "재연결이 거절돼 새 Deepgram 세션이 생기지 않는다"


def test_ready_take_is_accepted(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """READY→RUNNING 전이는 take 모듈(FE 의 started_at 갱신) 몫이라 순서를 강제하지 않는다."""
    ready_take = make_take(db_session, user.id, status="READY")
    with client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        assert handshake(ws, create_access_token(user.id))["take_id"] == str(ready_take.id)
        stop(ws)


# ── final 저장 ─────────────────────────────────────────────────────────


def test_only_finals_are_persisted_with_take_timeline(
    client: TestClient, token: str, store: FakeTranscriptStore
):
    use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 100, "안녕", is_final=False),
                    transcript(0, 200, "안녕하세요", is_final=True),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 5000))
        ws.receive_json()  # interim
        ws.send_bytes(frame(2, 5100))
        ws.receive_json()  # final
        stop(ws)

    (segment,) = store.segments
    assert store.take_ids == [TAKE_ID]
    assert (segment.seq, segment.stt_session_no) == (1, 1)
    assert (segment.start_ms, segment.end_ms) == (5000, 5200), "Take 기준 시각으로 저장한다"
    assert segment.transcript == "안녕하세요"
    assert segment.words[0].word == "안녕하세요" and segment.words[0].start_ms == 5000
    assert segment.speech_final is True


def test_finals_are_written_to_the_database(client: TestClient, db_session: Session, token: str):
    """가짜가 아니라 실제 take service · 테이블을 거친다.

    저장소를 테스트 세션에 묶어 같은 트랜잭션 안에서 결과를 본다."""
    use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 200, "첫 문장", is_final=True),
                    transcript(200, 400, "둘째 문장", is_final=True),
                ]
            )
        )
    )
    use_store(DbTranscriptStore(session_factory=lambda: db_session))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 1000))
        ws.receive_json()
        ws.send_bytes(frame(2, 1100))
        ws.receive_json()
        stop(ws)

    # take.id 가 아니라 상수를 쓴다 — 인가 단계의 rollback() 이 세션 객체를 expire 시키고
    # 저장소의 close() 가 떼어 내서(detached) 속성을 다시 읽을 수 없다
    rows = db_session.scalars(
        select(TakeTranscriptSegment)
        .where(TakeTranscriptSegment.take_id == TAKE_ID)
        .order_by(TakeTranscriptSegment.seq)
    ).all()
    assert [(r.seq, r.stt_session_no, r.transcript) for r in rows] == [
        (1, 1, "첫 문장"),
        (2, 1, "둘째 문장"),
    ]
    assert rows[0].start_ms == 1000 and rows[1].end_ms == 1400
    assert rows[0].words == [
        {
            "word": "첫 문장",
            "punctuated_word": "첫 문장",
            "start_ms": 1000,
            "end_ms": 1200,
            "confidence": 0.9,
        }
    ]
    assert take_service.transcript_cursor(db_session, TAKE_ID) == (2, 1)


def test_new_stream_resumes_numbering_from_persisted_cursor(client: TestClient, token: str):
    """grace 가 만료돼 스트림이 사라진 뒤 다시 붙으면 새 스트림이다. 번호가 1 로 돌아가면
    FE 가 앞부분 전사를 덮어쓰고 UNIQUE(take_id, seq) 도 깨진다 — 저장된 값에서 이어 받는다."""
    use(FakeSttAdapter(Plan(replies=[transcript(0, 100, "이어서", is_final=True)])))
    # 이전 스트림이 Deepgram 세션 2 에서 seq 5 까지 저장하고 사라졌다
    store = use_store(FakeTranscriptStore(cursor=(5, 2)))
    with client.websocket_connect(WS_PATH) as ws:
        ready = handshake(ws, token)
        assert ready["stt_session_no"] == 2
        assert wait_state(ws, "ok")["stt_session_no"] == 3
        ws.send_bytes(frame(1, 60_000))
        resumed = ws.receive_json()
        stop(ws)

    assert resumed["segment_id"] == "3-6"
    assert (store.segments[0].seq, store.segments[0].stt_session_no) == (6, 3)


def test_persist_failure_keeps_the_stream_alive(
    client: TestClient, token: str, caplog: pytest.LogCaptureFixture
):
    """DB 가 죽어도 발표는 계속된다. 화면 전사는 나가고 스트림은 다음 final 도 처리한다."""
    use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 100, "하나", is_final=True),
                    transcript(100, 200, "둘", is_final=True),
                ]
            )
        )
    )
    use_store(FakeTranscriptStore(fail=True))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        with caplog.at_level("ERROR"):
            ws.send_bytes(frame(1, 0))
            first = ws.receive_json()
            ws.send_bytes(frame(2, 100))
            second = ws.receive_json()
            status = stop(ws)

    assert (first["text"], second["text"]) == ("하나", "둘")
    assert status["state"] == "closed" and status["frames"] == 2
    assert "final 저장 실패" in caplog.text
    assert "하나" not in caplog.text, "전사 원문은 로그에 남기지 않는다"


@pytest.mark.anyio
async def test_stop_persists_the_last_final_even_if_the_client_dies_during_drain(
    db_session: Session, token: str
):
    """FE 가 stop 을 보낸 직후 탭을 닫아도(stt_status=closed 를 못 받아도) 서버 쪽 종료 절차는
    같다 — CloseStream 으로 마지막 전사를 받아 저장하고 스트림을 정리한다. 화면에 못 보낸
    건 조용히 실패하고, run() 은 예외를 새지 않는다."""
    adapter = FakeSttAdapter(Plan(replies=[transcript(0, 200, "마지막 말", is_final=True)]))
    store = FakeTranscriptStore()
    ws = FakeWebSocket(
        inbound=[
            {"type": "websocket.receive", "text": json.dumps({"type": "auth", "token": token})},
            {"type": "websocket.receive", "bytes": frame(1, 0)},
            {"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
        ],
        fail_send_after=2,  # ready 는 나가고, 그 뒤(마지막 transcript·closed 상태) 부터 끊긴다
    )
    realtime = service.RealtimeSession(
        ws, take_id=TAKE_ID, db=db_session, stt_adapter=adapter, transcript_store=store
    )

    await realtime.run()

    assert [json.loads(t)["type"] for t in ws.sent] == ["ready"]
    assert [seg.transcript for seg in store.segments] == ["마지막 말"]
    assert adapter.sessions[0].controls == ["CloseStream"]
    assert take_stream.active_count() == 0


# ── 코드 리뷰 반영: cancel 정리 상한 · reaper 참조 · take 별 락 ─────────


@pytest.mark.anyio
async def test_cancel_keeps_a_reference_to_the_reaper_task():
    """asyncio 는 태스크를 약하게만 잡는다. 참조 없이 create_task 만 하면 정리가 끝나기 전에
    GC 될 수 있다 (asyncio.create_task 문서의 경고)."""
    adapter = FakeSttAdapter(Plan(abort_delay=0.05))
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()

    assert isinstance(stream._reaper, asyncio.Task)
    assert await stream.wait_closed(timeout=1.0) is True
    await asyncio.wait_for(stream._reaper, 1.0)  # 참조가 있으니 끝까지 기다릴 수 있다


@pytest.mark.anyio
async def test_abort_is_bounded_so_cancel_cannot_hang_on_deepgram(
    monkeypatch: pytest.MonkeyPatch,
):
    """Deepgram 소켓 닫기는 shield 안에서 돌아 바깥에서 끊을 수 없다. 상대가 close 에 답하지
    않으면 상한을 두지 않는 한 영영 기다린다."""
    monkeypatch.setattr(take_stream, "ABORT_TIMEOUT_SEC", 0.1)
    adapter = FakeSttAdapter(Plan(abort_delay=10.0))  # 사실상 답이 없다
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()

    assert await stream.wait_closed(timeout=1.0) is True
    assert adapter.sessions[0].aborted is False, "답이 없어서 버리고 나왔다"


@pytest.mark.anyio
async def test_wait_cancelled_gives_up_and_reports_closed(monkeypatch: pytest.MonkeyPatch):
    """CANCEL_TIMEOUT_SEC 안에 정리가 안 끝나면 (코드 리뷰 케이스 1) 닫힌 것으로 확정한다.
    FE 는 더 붙잡히지 않고, 뒤늦게 끝난 정리는 idempotent 한 _finish 로 흡수된다."""
    monkeypatch.setattr(take_stream, "ABORT_TIMEOUT_SEC", 1.0)
    adapter = FakeSttAdapter(Plan(abort_delay=0.3))
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()
    assert await stream.wait_cancelled(0.05) is False
    assert stream.is_finished and stream.state == "closed"

    await asyncio.sleep(0.4)  # abort 가 뒤늦게 끝난다
    assert adapter.sessions[0].aborted is True
    assert stream.is_finished


@pytest.mark.anyio
async def test_handover_wait_does_not_block_other_takes(monkeypatch: pytest.MonkeyPatch):
    """attach 락이 프로세스 전역이면 한 Take 의 핸드오버 대기(HANDOVER_TIMEOUT_SEC) 동안
    다른 모든 Take 의 연결이 같이 멈춘다 (코드 리뷰 지적). take_id 별 락이어야 한다."""
    monkeypatch.setattr(take_stream, "HANDOVER_TIMEOUT_SEC", 0.5)
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan(), Plan())
    take_a, take_b, owner = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    def attach(take_id: uuid.UUID):
        return take_stream.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owner,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        )

    old_a = await attach(take_a)
    await _wait_until_ok(old_a)
    old_a.request_stop()  # swallow_close 라 스스로 안 끝난다 -> 재연결은 핸드오버를 기다린다

    async def attach_b_timed() -> tuple[float, TakeStream]:
        started = time.monotonic()
        stream = await attach(take_b)
        return time.monotonic() - started, stream

    new_a, (elapsed_b, stream_b) = await asyncio.gather(attach(take_a), attach_b_timed())

    assert elapsed_b < 0.25, f"다른 Take 가 핸드오버 대기에 같이 막혔다 ({elapsed_b:.2f}s)"
    assert new_a is not old_a and stream_b is not old_a
    assert take_stream.active_count() == 2
    assert not take_stream._attach_locks, "다 쓴 락은 남기지 않는다"


# ── 그 밖의 예외 케이스 ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_audio_after_stop_is_ignored():
    """stop 뒤의 오디오는 아무도 소비하지 않는다. 큐에 넣으면 통계(frames)만 어긋난다."""
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.push(frame(1, 0))
    stream.request_stop()
    stream.push(frame(2, 100))

    assert await stream.wait_closed(timeout=2.0)
    assert stream.sequencer.frames == 1


@pytest.mark.anyio
async def test_audio_that_never_reached_deepgram_is_reported_as_lost():
    """Deepgram 이 끝내 안 붙은 채 끝나면 큐에 남은 오디오는 유실이다. 마지막 stt_status 의
    lost_ms 가 이걸 빼먹으면 리포트가 "STT 다 받았다" 고 잘못 말한다."""
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(fail_times=1_000),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    for i in range(3):
        stream.push(frame(i + 1, i * 100))
    await asyncio.sleep(0.05)

    stream.cancel()
    assert await stream.wait_closed(timeout=1.0)
    assert stream.dropped_audio_ms == 300


@pytest.mark.anyio
async def test_unexpected_exception_in_session_reconnects_instead_of_dying(
    caplog: pytest.LogCaptureFixture,
):
    """어댑터 버그 같은 예상 못 한 예외로 태스크가 죽으면 FE 는 아무 알림 없이 STT 만 조용히
    멈춘 상태가 된다. Deepgram 이 끊긴 것과 같게 다뤄 재접속해야 한다."""
    adapter = FakeSttAdapter(Plan(explode_on_send=True), Plan())
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    with caplog.at_level("ERROR"):
        stream.push(frame(1, 0))  # 첫 세션이 터진다
        deadline = time.monotonic() + 2.0
        while (len(adapter.sessions) < 2 or stream.state != "ok") and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    assert len(adapter.sessions) == 2 and stream.state == "ok"
    assert "예상 못 한 예외" in caplog.text
    stream.push(frame(2, 100))
    stream.request_stop()
    assert await stream.wait_closed(timeout=2.0)
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]
