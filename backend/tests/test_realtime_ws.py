"""WebSocket 엔드포인트와 Take 스트림. Deepgram 과 전사 저장소만 가짜로 바꾸고 나머지는 실제로 돈다.

인증은 실제 JWT 로, 사용자·Take 조회는 테스트 DB 로. 프레임 파싱·offset·무음 채우기·재연결·
번호 연속성·종료 순서가 전부 실제 코드를 거친다. 저장소는 기본이 가짜(메모리) 이고,
실제 DB 저장은 `DbTranscriptStore` 를 테스트 세션에 묶어 따로 확인한다.
"""

import asyncio
import json
import struct
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosedError

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO
from pitch_coach_backend.module.take.entity import Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import DuplicateTranscriptSegment, NonExistentTake
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime import service, take_stream
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.dependencies import get_stt_adapter, get_transcript_store
from pitch_coach_backend.realtime.dto import (
    AutoEndReason,
    ErrorMessage,
    TranscriptMessage,
    WsErrorCode,
)
from pitch_coach_backend.realtime.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt_adapter import (
    Metadata,
    SttConfig,
    SttConfigRejected,
    SttConnectError,
    SttEvent,
    Transcript,
    Word,
)
from pitch_coach_backend.realtime.take_stream import FAST_ATTEMPTS, TakeStream
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
    def __init__(self, *plan: Plan, fail_times: int = 0, reject_times: int = 0) -> None:
        self.plan = list(plan)
        self.fail_times = fail_times
        # 처음 이만큼은 Deepgram 이 설정을 400 으로 거절한 것처럼 군다
        self.reject_times = reject_times
        self.configs: list[SttConfig] = []
        self.sessions: list[FakeSttSession] = []

    async def connect(self, config: SttConfig) -> FakeSttSession:
        self.configs.append(config)
        if len(self.configs) <= self.reject_times:
            raise SttConfigRejected("400 Keyterm limit exceeded.")
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

    def __init__(
        self,
        *,
        cursor: tuple[int, int] = (0, 0),
        fail: bool = False,
        errors: Iterable[Exception] = (),
        fail_mark: bool = False,
        append_delay: float = 0.0,
        marks: bool = True,
    ) -> None:
        self.segments: list[TranscriptSegmentCreateDTO] = []
        self.take_ids: list[uuid.UUID] = []
        self.ended: list[uuid.UUID] = []
        # 자동 종료로 적은 (Take, 이유)
        self.auto_ended: list[tuple[uuid.UUID, str]] = []
        # 떠난 것으로 적은 (Take, 끝난 시각)
        self.abandoned: list[tuple[uuid.UUID, datetime]] = []
        # running_takes() 가 돌려줄 (Take, 마지막 활동 시각)
        self.running: list[tuple[uuid.UUID, datetime]] = []
        # 저장한 순서. 종료를 마지막 전사 뒤에 적는지 본다
        self.calls: list[str] = []
        # cursor 를 읽을 때마다 그때까지 저장된 구간 수. 새 스트림이 저장 뒤에 번호를 읽는지 본다
        self.cursor_reads: list[int] = []
        self._cursor = cursor
        self.fail = fail
        # 저장할 때마다 앞에서 하나씩 꺼내 던진다. 다 쓰면 정상으로 저장한다
        self.errors = list(errors)
        self.fail_mark = fail_mark
        # 저장이 이만큼 걸린다 (저장 도중 정리가 오는 경우 재현)
        self.append_delay = append_delay
        # False 면 Take 가 없거나 이미 끝나 있어 아무것도 적지 않은 것처럼 군다
        self.marks = marks

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        self.cursor_reads.append(len(self.segments))
        return self._cursor

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await asyncio.sleep(self.append_delay)  # 실제 저장은 threadpool 왕복이라 한 번은 양보한다
        if self.fail:
            raise RuntimeError("db down")
        if self.errors:
            raise self.errors.pop(0)
        self.take_ids.append(take_id)
        self.segments.append(segment)
        self.calls.append("append")

    async def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
        await asyncio.sleep(0)
        if self.fail_mark:
            raise RuntimeError("db down")
        # DB 처럼 이미 끝난 Take 는 다시 적지 않는다
        if not self.marks or take_id in self.ended:
            return False
        self.ended.append(take_id)
        self.calls.append("mark_ended")
        if end_reason is not None:
            self.auto_ended.append((take_id, end_reason))
        return True

    async def mark_abandoned(self, take_id: uuid.UUID, ended_at: datetime) -> bool:
        await asyncio.sleep(0)
        if self.fail_mark:
            raise RuntimeError("db down")
        if not self.marks or take_id in self.ended:
            return False
        self.ended.append(take_id)
        self.calls.append("mark_abandoned")
        self.abandoned.append((take_id, ended_at))
        return True

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        return list(self.running)


# ── fixture ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_leftover_streams():
    """스트림은 연결보다 오래 산다. 테스트 사이에 넘어가면 죽은 이벤트 루프의 태스크가 남는다."""
    yield
    for stream in list(take_stream._streams.values()):
        stream.cancel()
    take_stream._streams.clear()
    take_stream._attach_locks.clear()
    take_stream._ended.clear()
    take_stream._takes.clear()
    for timer in take_stream._abandon_timers.values():
        timer.cancel()
    take_stream._abandon_timers.clear()
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
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="deck.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        status=status,
        goal_time_sec=300,
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
    with client.websocket_connect(WS_PATH, headers={"origin": settings.frontend_base_url}) as ws:
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


def test_script_terms_follow_fillers_in_keyterms(
    client: TestClient, stt: FakeSttAdapter, token: str, take: Take, db_session: Session
):
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = ["SeatFlow", "음"]
    db_session.add(
        ScriptSlide(
            script_version_id=script.id,
            slide_number=1,
            full_content="본문",
            keywords=["좌석 예측"],
        )
    )
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

    (config,) = stt.configs
    # filler 가 먼저, 대본 terms 가 뒤. filler 와 겹치는 "음" 은 한 번만.
    # 슬라이드 keywords 는 넣지 않는다
    assert config.keyterms == (*KEYTERM_FILLERS, "SeatFlow")
    assert config.min_keyterms == len(KEYTERM_FILLERS)


def test_rejected_keyterms_drop_five_from_the_end_without_backoff(
    client: TestClient, token: str, take: Take, db_session: Session
):
    # 토큰 추정이 빗나가 Deepgram 이 거절하면 우선순위 낮은 뒤쪽 용어부터 5개씩 뺀다.
    # 거절은 Deepgram 장애가 아니라 바로 다시 붙는다 — 실패로 세면 backoff·degraded 가 끼어든다
    terms = [f"용어{i:02d}" for i in range(12)]
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = terms
    db_session.commit()
    adapter = use(FakeSttAdapter(reject_times=FAST_ATTEMPTS))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        first_status = ws.receive_json()
        assert first_status["state"] == "ok", first_status
        assert first_status["stt_session_no"] == 1

    assert [config.keyterms for config in adapter.configs] == [
        (*KEYTERM_FILLERS, *terms),
        (*KEYTERM_FILLERS, *terms[:7]),
        (*KEYTERM_FILLERS, *terms[:2]),
        KEYTERM_FILLERS,
    ]


def test_rejected_keyterms_never_drop_fillers(
    client: TestClient, token: str, take: Take, db_session: Session
):
    # 대본 용어를 다 빼고도 거절되면 filler 는 그대로 두고 평소처럼 backoff 재시도한다
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = ["SeatFlow"]
    db_session.commit()
    adapter = use(FakeSttAdapter(reject_times=2))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    first, *rest = adapter.configs
    assert first.keyterms == (*KEYTERM_FILLERS, "SeatFlow")
    assert [config.keyterms for config in rest] == [KEYTERM_FILLERS, KEYTERM_FILLERS]


def test_fillers_only_config_has_nothing_to_trim(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    # 대본 용어가 없으면 줄일 것도 없다
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

    (config,) = stt.configs
    assert config.keyterms == KEYTERM_FILLERS
    assert config.min_keyterms == len(KEYTERM_FILLERS)


def test_script_term_lookup_failure_still_connects(
    client: TestClient, stt: FakeSttAdapter, token: str, monkeypatch: pytest.MonkeyPatch
):
    # 용어 조회는 부가 기능이다. 실패해도 연결을 닫지 않고 filler 만으로 STT 를 연다
    def broken(*args, **kwargs):
        raise RuntimeError("statement timeout")

    monkeypatch.setattr(service.pitch_service, "stt_keyterm_candidates", broken)

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    (config,) = stt.configs
    assert config.keyterms == KEYTERM_FILLERS


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


def test_reload_within_grace_restarts_the_timeline_instead_of_dropping_audio(
    client: TestClient, token: str
):
    """탭을 새로고침하면 FE 는 무대 시계와 함께 seq·offset 을 1·0 부터 다시 센다.
    grace 안에 붙어 같은 스트림을 쓰더라도 그 프레임을 역행으로 버리면 안 된다 —
    예전 seq 를 넘을 때까지 오디오가 통째로 사라진다."""
    adapter = use(
        FakeSttAdapter(
            Plan(replies=[transcript(0, 100, "앞부분", is_final=True)]),
            Plan(replies=[transcript(0, 100, "새로고침한 뒤", is_final=True)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(3):
            ws.send_bytes(frame(i + 1, i * 100))
        assert ws.receive_json()["segment_id"] == "1-1"

    # 새로고침. 같은 Take 에 grace 안에 다시 붙는데 seq·offset 이 처음부터다
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 100))
        ws.send_text(json.dumps({"type": "stop"}))
        messages = []
        while not (messages and messages[-1].get("state") == "closed"):
            messages.append(ws.receive_json())

    transcripts = [m for m in messages if m["type"] == "transcript"]
    status = messages[-1]

    # offset 이 0 으로 돌아갔으니 Deepgram 세션을 갈아 base 를 다시 잡는다
    assert len(adapter.sessions) == 2
    assert [len(a) for a in adapter.sessions[1].audio] == [3200, 3200]
    # 세그먼트 번호는 이어지고, 시각은 새 무대 시계 기준이다
    assert [(t["segment_id"], t["start_ms"], t["text"]) for t in transcripts] == [
        ("2-2", 0, "새로고침한 뒤")
    ]
    assert status["frames"] == 5
    assert status["dropped_frames"] == 0


def test_grace_expiry_closes_the_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch, store: FakeTranscriptStore
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
    # 스트림만 정리한다. FE 는 끊긴 뒤에도 다시 붙으니 Take 는 끝내지 않는다
    assert store.ended == []
    assert TAKE_ID not in take_stream._ended
    # 대신 아무도 다시 붙지 않으면 끝낼 타이머를 건다
    assert TAKE_ID in take_stream._abandon_timers


def test_reconnect_after_grace_expiry_resumes_on_a_new_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """FE 는 빠른 백오프가 다 실패해도 포기하지 않고 5초마다 다시 붙는다. 그래서 grace 가
    지난 뒤에 붙는 연결도 받아야 한다 — 새 스트림을 열고, FE 가 리셋하지 않은 seq 와
    Take 기준 offset 을 그대로 받아 전사 시각이 Take 타임라인에 맞게 나온다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    adapter = use(
        FakeSttAdapter(Plan(), Plan(replies=[transcript(0, 100, "다시 붙었다", is_final=True)]))
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))

    deadline = time.monotonic() + 3.0
    while take_stream.active_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert take_stream.active_count() == 0

    # 40초 뒤에 붙었다. 그동안 FE 는 seq 를 계속 올렸다 (끊긴 동안의 프레임은 버려졌다)
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(400, 40_000))
        resumed = ws.receive_json()
        stop(ws)

    assert len(adapter.sessions) == 2
    assert (resumed["type"], resumed["is_final"]) == ("transcript", True)
    assert resumed["start_ms"] == 40_000


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


def test_reconnect_while_stopping_is_rejected(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """사용자가 끝낸 Take 다. 마지막 전사를 정리하는 동안(DB 에 적기 전) 붙는 연결도 받지 않는다 —
    받으면 새 스트림이 열려 끝난 연습 뒤에 전사가 더 붙는다. 종료 중 새로고침한 FE 가 stop 만
    보내러 붙어도 이렇게 거절되고, FE 는 stop 을 보낸 상태에서 닫히면 종료를 그대로 이어 간다."""
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.3)
    adapter = use(FakeSttAdapter(Plan(swallow_close=True)))

    with client.websocket_connect(WS_PATH) as first:
        handshake(first, token)
        wait_state(first, "ok")
        first.send_text(json.dumps({"type": "stop"}))  # 응답을 기다리지 않는다
        deadline = time.monotonic() + 3.0
        while TAKE_ID not in take_stream._ended and time.monotonic() < deadline:
            time.sleep(0.01)

        with client.websocket_connect(WS_PATH) as second:
            auth(second, token)
            assert second.receive_json()["code"] == "TAKE_ENDED"
            assert closed_with(second) == 1008

        wait_state(first, "closed")

    assert len(adapter.sessions) == 1, "새 Deepgram 세션을 열지 않는다"


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
    db_session: Session, user: User, token: str, monkeypatch: pytest.MonkeyPatch
):
    """ready 전송·resend_missed·pump_client 가 하나의 try/finally 로 묶여야 한다.
    그중 아무거나 먼저 실패해도 detach() 가 불려야 스트림에 grace 타이머가 걸린다
    (오디오를 한 번도 안 받았으면 바로 정리된다).
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
    # 붙인 스트림을 잡아 둔다. 오디오 없이 끊기면 바로 정리돼 레지스트리에서 빠질 수 있다
    attached: list[TakeStream] = []
    attach = take_stream.attach

    async def spy(*args, **kwargs) -> TakeStream:
        stream = await attach(*args, **kwargs)
        attached.append(stream)
        return stream

    monkeypatch.setattr(take_stream, "attach", spy)

    await realtime.run()  # 예외가 새지 않는다

    stream = attached[0]
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
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.05)
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
    """옛 스트림의 정리가 정상 종료 상한(DRAIN_TIMEOUT_SEC)을 넘기면 핸드오버는 기다리기를
    포기한다. 그때 그냥 forget() 만 하면 옛 스트림은 레지스트리에서만 사라질 뿐 자기 페이스대로
    계속 돈다 — Deepgram 연결이 안 끊긴다. cancel() 로 강제로 끊어야 한다."""
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.05)
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
    await _wait_until_ok(new_stream)
    await asyncio.sleep(0.01)  # 세션 태스크가 뜨기 전에 끊으면 "never awaited" 경고가 난다
    new_stream.cancel()
    await new_stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_handover_waits_for_the_old_stream_to_finish_saving(
    monkeypatch: pytest.MonkeyPatch,
):
    """정리 중인 스트림이 final 을 저장하는 도중에 끊겨도 그 저장은 끝까지 간다. 새 스트림은
    그 저장이 끝난 뒤 번호를 읽는다 — 먼저 읽으면 같은 seq 로 저장하다 UNIQUE 위반으로 버려진다."""
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    adapter = FakeSttAdapter(
        Plan(replies=[transcript(0, 200, "마지막 말", is_final=True)], swallow_close=True),
        Plan(),
    )
    store = FakeTranscriptStore(append_delay=0.3)

    old_stream = await take_stream.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
    )
    await _wait_until_ok(old_stream)
    old_stream.push(frame(1, 0))
    await _wait_for(lambda: adapter.sessions[0].audio)
    old_stream.request_stop()  # final 저장(0.3s) 중에 drain 상한(0.05s)이 지나 끊긴다

    new_stream = await take_stream.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
    )

    assert [seg.transcript for seg in store.segments] == ["마지막 말"]
    assert store.cursor_reads == [0, 1], "새 스트림이 옛 스트림의 저장보다 먼저 번호를 읽었다"
    await _wait_until_ok(new_stream)
    await asyncio.sleep(0.01)  # 세션 태스크가 뜨기 전에 끊으면 "never awaited" 경고가 난다
    new_stream.cancel()
    await new_stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_finals_received_before_deepgram_closes_are_saved():
    """Deepgram 이 먼저 끊으면 오디오 쪽이 그걸 먼저 알 수 있다. 그때 이벤트 쪽을 바로 끊으면
    이미 받아 둔 final 이 화면에도 DB 에도 남지 않는다. 이벤트 쪽이 남은 것을 다 읽게 둔다."""
    adapter = FakeSttAdapter(
        Plan(replies=[transcript(0, 200, "끊기 전 말", is_final=True)], die_after=1),
        Plan(),
    )
    store = FakeTranscriptStore()
    stream = TakeStream(
        uuid.uuid7(), owner_id=uuid.uuid7(), stt_adapter=adapter, config=SttConfig(), store=store
    )
    stream.start()
    await _wait_until_ok(stream)

    # 두 프레임을 한 번에 넣는다. 첫 프레임 뒤 Deepgram 이 final 을 주고 끊기고, 두 번째 프레임을
    # 보내다 오디오 쪽이 끊긴 것을 안다 — 이벤트 쪽이 final 을 읽기 전이다
    stream.push(frame(1, 0))
    stream.push(frame(2, 100))
    await _wait_for(lambda: len(adapter.sessions) == 2)

    assert [seg.transcript for seg in store.segments] == ["끊기 전 말"]
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


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
        # 오디오를 한 번도 안 받은 스트림은 끊기면 바로 정리된다. grace 에 들어가려면
        # 받은 오디오가 있어야 한다
        ws.send_bytes(frame(1, 0))
    assert take_stream.active_count() == 1, "grace 중이라 스트림은 살아 있다"

    take.status = "COMPLETED"
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(stt.sessions) == 1, "재연결이 거절돼 새 Deepgram 세션이 생기지 않는다"


def test_ready_take_becomes_running_on_auth(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """첫 연결이 Take 를 시작한다 — 인가를 통과하면 READY 를 RUNNING 으로 바꾼다."""
    ready_take = make_take(db_session, user.id, status="READY")
    with client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        assert handshake(ws, create_access_token(user.id))["take_id"] == str(ready_take.id)
        stop(ws)

    db_session.refresh(ready_take)
    assert ready_take.status == "RUNNING"


def test_running_write_failure_still_connects(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """RUNNING 을 못 적어도 STT 는 돈다."""

    def fail(db: Session, take_id: uuid.UUID) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(take_service, "mark_running", fail)
    ready_take = make_take(db_session, user.id, status="READY")
    with caplog.at_level("ERROR"), client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        handshake(ws, create_access_token(user.id))
        wait_state(ws, "ok")
        stop(ws)

    assert "RUNNING 으로 못 바꿨다" in caplog.text


# ── Take 종료 ─────────────────────────────────────────────────────────


def test_stop_ends_the_take_after_the_last_final(
    client: TestClient, token: str, store: FakeTranscriptStore
):
    """끝내기는 마지막 확정 전사를 저장한 뒤에 Take 를 끝난 것으로 적는다. Take 뒤 분석은 그걸 보고
    시작하므로 전사가 다 있어야 한다."""
    use(FakeSttAdapter(Plan(replies=[transcript(0, 100, "마지막 문장", is_final=True)])))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        # 답을 기다리지 않고 바로 끝낸다 — 마지막 final 은 정리하는 동안 온다
        ws.send_bytes(frame(1, 0))
        ws.send_text(json.dumps({"type": "stop"}))
        seen = []
        while True:
            message = ws.receive_json()
            seen.append(message["type"])
            if message.get("state") == "closed":
                break
        assert closed_with(ws) == 1000

    assert seen == ["transcript", "stt_status"]
    assert store.calls == ["append", "mark_ended"]
    assert store.ended == [TAKE_ID]


def test_reconnect_after_stop_is_rejected(client: TestClient, token: str):
    """끝낸 Take 에 다시 붙으면 새 STT 세션을 열지 않는다. DB 에 적히기 전이어도 막는다 — 가짜
    저장소는 DB 를 바꾸지 않아 Take 가 RUNNING 그대로다."""
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        stop(ws)

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json() == {
            "type": "error",
            "code": "TAKE_ENDED",
            "message": "이미 끝난 연습입니다.",
        }
        assert closed_with(ws) == 1008
    assert len(adapter.sessions) == 1


def test_end_write_failure_still_closes_the_take(
    client: TestClient, token: str, caplog: pytest.LogCaptureFixture
):
    """DB 가 죽어 종료를 못 적어도 FE 에는 평소처럼 closed 를 보내고 닫는다. 이 프로세스 안에서는
    다시 붙는 연결을 계속 막는다."""
    use_store(FakeTranscriptStore(fail_mark=True))
    adapter = use(FakeSttAdapter())
    with caplog.at_level("ERROR"), client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        stop(ws)
        assert closed_with(ws) == 1000
    assert "Take 종료 저장 실패" in caplog.text

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(adapter.sessions) == 1


def test_take_status_moves_ready_running_analyzing_in_the_database(
    db_session: Session, user: User
):
    """연결이 READY 를 RUNNING 으로, 끝내기가 ANALYZING 으로 바꾼다. 두 번 불러도 한 번만 바뀐다."""
    take = make_take(db_session, user.id, status="READY")
    assert take_service.mark_running(db_session, take.id) is True
    assert take_service.mark_running(db_session, take.id) is False, "다시 붙은 연결은 그대로 둔다"
    db_session.refresh(take)
    assert take.status == "RUNNING"

    assert take_service.mark_ended(db_session, take.id) is True
    assert take_service.mark_ended(db_session, take.id) is False
    db_session.refresh(take)
    assert take.status == "ANALYZING"
    assert take.ended_at is not None, "FE 가 종료 시각을 못 적어도 남는다"
    assert take_service.mark_running(db_session, take.id) is False, "끝난 Take 는 다시 열지 않는다"
    assert take_service.mark_ended(db_session, uuid.uuid7()) is False

    # 연결 때 RUNNING 을 못 적었어도 끝낼 수 있다
    never_started = make_take(db_session, user.id, status="READY")
    assert take_service.mark_ended(db_session, never_started.id) is True


def test_end_leaves_a_finished_take_and_the_fe_end_time(db_session: Session, user: User):
    """이미 끝난 Take 는 바꾸지 않는다. FE 가 먼저 적은 종료 시각도 덮지 않는다."""
    completed = make_take(db_session, user.id, status="COMPLETED")
    assert take_service.mark_ended(db_session, completed.id) is False
    db_session.refresh(completed)
    assert completed.status == "COMPLETED"

    running = make_take(db_session, user.id)
    fe_end = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
    running.ended_at = fe_end
    db_session.commit()
    assert take_service.mark_ended(db_session, running.id) is True
    db_session.refresh(running)
    assert running.ended_at == fe_end


@pytest.mark.anyio
async def test_db_store_marks_the_take_ended(db_session: Session, take: Take):
    store = DbTranscriptStore(session_factory=lambda: db_session)
    assert await store.mark_ended(TAKE_ID) is True
    assert db_session.scalar(select(Take.status).where(Take.id == TAKE_ID)) == "ANALYZING"
    assert await store.mark_ended(TAKE_ID) is False


# ── stop 없이 끝내기 (Take PUT) ───────────────────────────────────────


def put_end(client: TestClient, take: Take, token: str) -> int:
    """FE 종료 흐름의 Take PUT. 마이크를 못 열었거나 재연결을 포기한 FE 는 stop 없이 이것만 보낸다."""
    response = client.put(
        f"/api/pitches/{take.pitch_id}/takes/{take.id}",
        json={"ended_at": "2026-10-22T10:05:00Z"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.status_code


def test_put_end_while_connected_leaves_it_to_stop(
    client: TestClient, db_session: Session, take: Take, token: str, store: FakeTranscriptStore
):
    """연결이 붙어 있으면 그 연결의 stop 이 마지막 전사를 저장한 뒤 끝낸다. PUT 이 먼저 적지 않는다."""
    use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        assert put_end(client, take, token) == 200
        db_session.refresh(take)
        assert take.status == "RUNNING"
        stop(ws)

    assert store.ended == [TAKE_ID]


def test_put_end_after_the_connection_dropped_drains_then_ends(
    client: TestClient, db_session: Session, take: Take, token: str, store: FakeTranscriptStore
):
    """끊긴 뒤 30초 안에 PUT 으로 끝내면 stop 처럼 남은 전사를 받아 두고 스트림을 정리한 뒤 끝낸다."""
    adapter = use(FakeSttAdapter(Plan(replies=[transcript(0, 100, "마지막", is_final=True)])))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.receive_json()
    stream = take_stream._streams[TAKE_ID]
    deadline = time.monotonic() + 2.0
    while stream.has_client and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not stream.has_client

    assert put_end(client, take, token) == 200

    assert take_stream.active_count() == 0
    assert adapter.session is not None
    assert adapter.session.controls == ["CloseStream"]
    assert store.calls == ["append"]
    db_session.refresh(take)
    assert take.status == "ANALYZING"
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(adapter.sessions) == 1


@pytest.mark.anyio
async def test_end_without_stop_leaves_a_stopping_take_to_its_connection():
    """stop 으로 끝내는 중이면 그 연결이 마지막 전사를 저장한 뒤 적는다. 정리가 끝나 스트림이
    사라졌으면 PUT 이 다시 적어도 된다 — 같은 Take 를 두 번 끝내도 한 번만 바뀐다."""
    tab = FakeWebSocket()
    stream = TakeStream(
        TAKE_ID,
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(Plan(swallow_close=True)),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    take_stream._streams[TAKE_ID] = stream
    stream.start()
    await stream.attach(tab)
    stream.push(frame(1, 0), source=tab)
    stream.end()
    stream.detach(tab)

    assert await take_stream.end_without_stop(TAKE_ID) is False

    stream.cancel()
    await stream.wait_closed(timeout=1.0)
    assert await take_stream.end_without_stop(TAKE_ID) is True


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
    assert "저장하지 못한 final 을 버린다" in caplog.text and "count=2 seq=1~2" in caplog.text
    assert "하나" not in caplog.text, "전사 원문은 로그에 남기지 않는다"


def test_failed_final_is_saved_with_the_next_one(client: TestClient, token: str):
    """DB 가 잠깐 죽었다 돌아오면 그사이 저장 못 한 final 을 다음 final 앞에 저장한다."""
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
    store = use_store(FakeTranscriptStore(errors=[RuntimeError("db down")]))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.receive_json()
        ws.send_bytes(frame(2, 100))
        ws.receive_json()
        stop(ws)

    assert [(seg.seq, seg.transcript) for seg in store.segments] == [(1, "하나"), (2, "둘")]


def test_failed_last_final_is_saved_before_the_take_ends(client: TestClient, token: str):
    """마지막 final 저장이 실패해도 스트림이 끝날 때 한 번 더 저장한다. Take 종료는 그 뒤에
    적는다."""
    use(FakeSttAdapter(Plan(replies=[transcript(0, 100, "마지막", is_final=True)])))
    store = use_store(FakeTranscriptStore(errors=[RuntimeError("db down")]))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.receive_json()
        stop(ws)

    assert [seg.transcript for seg in store.segments] == ["마지막"]
    assert store.calls == ["append", "mark_ended"]


def test_final_already_committed_does_not_block_the_rest(
    client: TestClient, token: str, caplog: pytest.LogCaptureFixture
):
    """실패로 알았던 저장이 실제로는 커밋됐으면 다시 저장할 때 번호가 겹친다. 저장된 것으로 보고
    넘어가야 뒤의 final 이 막히지 않는다."""
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
    store = use_store(
        FakeTranscriptStore(errors=[RuntimeError("db down"), DuplicateTranscriptSegment()])
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        with caplog.at_level("WARNING"):
            ws.send_bytes(frame(1, 0))
            ws.receive_json()
            ws.send_bytes(frame(2, 100))
            ws.receive_json()
            stop(ws)

    assert [(seg.seq, seg.transcript) for seg in store.segments] == [(2, "둘")]
    assert "이미 저장된 final" in caplog.text
    assert "저장하지 못한 final 을 버린다" not in caplog.text


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


# ── Take 삭제 ─────────────────────────────────────────────────────────


def wait_until(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition()


def test_deleting_the_take_cuts_its_live_stream(
    client: TestClient, stt: FakeSttAdapter, token: str, take: Take
):
    """진행 중인 Take 를 지우면 Deepgram 세션도 바로 끊는다. 두면 FE 가 떠날 때까지 요금이 나가고
    final 마다 저장이 외래 키 위반으로 실패한다. 저장할 곳이 없으니 남은 전사도 기다리지 않는다."""
    pitch_id = take.pitch_id
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))

        response = client.delete(
            f"/api/pitches/{pitch_id}/takes/{TAKE_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        # 다시 붙을 때 받을 것과 같은 코드다. FE 는 이걸 받으면 다시 붙지 않는다
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008

    session = stt.session
    assert session is not None
    wait_until(lambda: session.aborted)
    assert session.controls == [], "CloseStream 으로 남은 전사를 기다리지 않는다"
    assert take_stream.active_count() == 0


def test_deleting_the_pitch_cuts_the_live_streams_of_its_takes(
    client: TestClient, stt: FakeSttAdapter, token: str, take: Take
):
    """발표를 지우면 Take 가 CASCADE 로 같이 지워진다. 그 Take 의 스트림도 같이 끊는다."""
    pitch_id = take.pitch_id
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        response = client.delete(
            f"/api/pitches/delete/{pitch_id}", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008

    session = stt.session
    assert session is not None
    wait_until(lambda: session.aborted)
    assert take_stream.active_count() == 0


def test_final_for_a_deleted_take_cuts_the_stream(
    client: TestClient, db_session: Session, token: str, caplog: pytest.LogCaptureFixture
):
    """지운 요청이 스트림을 못 끊었어도(커밋과 끊기 사이에 온 final, 다른 프로세스) 저장하다 Take 가
    없다는 걸 알면 스스로 끊는다. final 마다 외래 키 오류를 ERROR 로 남기지 않는다."""
    adapter = use(FakeSttAdapter(Plan(replies=[transcript(0, 200, "지운 뒤", is_final=True)])))
    use_store(DbTranscriptStore(session_factory=lambda: db_session))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        # REST 를 거치지 않고 지운다. 스트림은 지워진 줄 모른다
        db_session.execute(sql_delete(Take).where(Take.id == TAKE_ID))
        db_session.commit()

        with caplog.at_level("WARNING"):
            ws.send_bytes(frame(1, 0))
            final = ws.receive_json()
            error = ws.receive_json()
            code = closed_with(ws)

    assert final["text"] == "지운 뒤", "화면에는 저장보다 먼저 나간다"
    assert (error["code"], code) == ("TAKE_NOT_FOUND", 1008)
    assert "Take 가 지워져" in caplog.text
    assert "final 저장 실패" not in caplog.text
    session = adapter.session
    assert session is not None
    wait_until(lambda: session.aborted)
    assert take_stream.active_count() == 0


def test_only_a_missing_take_is_reported_as_deleted(db_session: Session, take: Take):
    """외래 키 위반만 'Take 가 없다' 로 바꾼다. 번호가 겹친 것(UNIQUE)까지 바꾸면 스트림이
    살아 있는 Take 를 지워진 것으로 알고 끊는다."""
    segment = TranscriptSegmentCreateDTO(
        seq=1,
        stt_session_no=1,
        start_ms=0,
        end_ms=100,
        transcript="하나",
        words=[],
        confidence=0.9,
        speech_final=True,
    )
    with pytest.raises(NonExistentTake):
        take_service.append_transcript_segment(db_session, uuid.uuid7(), segment)
    db_session.rollback()

    take_service.append_transcript_segment(db_session, TAKE_ID, segment)
    with pytest.raises(DuplicateTranscriptSegment):
        take_service.append_transcript_segment(db_session, TAKE_ID, segment)


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
    """attach 락이 프로세스 전역이면 한 Take 의 핸드오버 대기(DRAIN_TIMEOUT_SEC) 동안
    다른 모든 Take 의 연결이 같이 멈춘다 (코드 리뷰 지적). take_id 별 락이어야 한다."""
    monkeypatch.setattr(take_stream, "DRAIN_TIMEOUT_SEC", 0.5)
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


@pytest.mark.anyio
async def test_frames_from_a_taken_over_connection_are_ignored():
    """다른 탭이 이어받은 뒤 쫓겨난 연결이 닫히기 전에 보낸 프레임은 버린다.
    섞이면 새 탭의 seq 를 앞질러 새 탭 오디오가 역행으로 버려진다."""
    adapter = FakeSttAdapter()
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    stream.push(frame(1, 0), source=old_tab)
    stream.push(frame(2, 100), source=old_tab)

    await stream.attach(new_tab)
    stream.push(frame(3, 200), source=old_tab)  # 쫓겨난 탭이 마지막으로 보낸 것
    stream.push(frame(1, 0), source=new_tab)
    stream.push(frame(2, 100), source=new_tab)

    assert stream.sequencer.frames == 4
    assert stream.sequencer.dropped == 0
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_stop_from_a_taken_over_connection_is_ignored():
    """쫓겨난 연결이 닫히기 전에 보낸 stop 도 버린다. 받으면 스트림이 멈추고, 이어받은 탭의
    오디오는 멈춘 스트림에 아무 알림 없이 버려진다."""
    adapter = FakeSttAdapter()
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab = FakeWebSocket(
        inbound=[{"type": "websocket.receive", "text": json.dumps({"type": "stop"})}]
    )
    new_tab = FakeWebSocket()
    await stream.attach(old_tab)
    await stream.attach(new_tab)

    realtime = service.RealtimeSession(
        old_tab,
        take_id=stream.take_id,
        db=None,  # type: ignore[arg-type] - 펌프는 DB 를 안 본다
        stt_adapter=adapter,
        transcript_store=FakeTranscriptStore(),
    )
    await realtime._pump_client(stream)

    assert not stream.is_stopping
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_stream_without_audio_stops_on_detach_instead_of_waiting_grace():
    """오디오를 한 번도 안 받은 스트림은 끊기면 바로 정리한다. 살려 둘 전사가 없는데 grace 동안
    Deepgram 세션만 붙든다 — 종료 중에 새로고침한 FE 가 stop 만 보내러 붙었다 끊은 경우."""
    adapter = FakeSttAdapter()

    def make() -> TakeStream:
        return TakeStream(
            uuid.uuid7(),
            owner_id=uuid.uuid7(),
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        )

    silent, spoken = make(), make()
    silent_tab, spoken_tab = FakeWebSocket(), FakeWebSocket()
    await silent.attach(silent_tab)
    await spoken.attach(spoken_tab)
    spoken.push(frame(1, 0), source=spoken_tab)

    silent.detach(silent_tab)
    spoken.detach(spoken_tab)

    assert silent.is_stopping
    assert not spoken.is_stopping, "오디오를 받은 스트림은 grace 동안 재연결을 기다린다"
    for stream in (silent, spoken):
        stream.cancel()
        await stream.wait_closed(timeout=1.0)


def _slow_after_first(adapter: FakeSttAdapter, delay: float) -> None:
    """첫 세션 뒤로는 Deepgram 연결이 delay 초 걸린다 (세션을 가는 동안 오디오가 큐에 쌓인다)."""
    connect = adapter.connect

    async def slow(config: SttConfig) -> FakeSttSession:
        if adapter.sessions:
            await asyncio.sleep(delay)
        return await connect(config)

    adapter.connect = slow  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_audio_dropped_while_rotating_is_filled_with_silence(
    monkeypatch: pytest.MonkeyPatch,
):
    """세션을 가는 동안 큐가 넘쳐 드롭된 것은 넘겨받은 프레임 **뒤**의 오디오다. 새 세션의
    base 가 흡수하면 안 된다 — 그만큼 이후 전사 시각이 앞당겨진다. 새로고침할 때마다 세션을 간다."""
    monkeypatch.setattr(take_stream, "QUEUE_MAX_FRAMES", 10)
    adapter = FakeSttAdapter()
    _slow_after_first(adapter, 0.3)
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    for i in range(30):
        stream.push(frame(i + 1, i * 100), source=old_tab)
        await asyncio.sleep(0)
    await _wait_for(lambda: adapter.sessions[0].audio_ms == 3000)

    # 새로고침. seq 는 1 부터, offset 은 이어받은 무대 시계부터
    await stream.attach(new_tab)
    for i in range(5):
        stream.push(frame(i + 1, 3000 + i * 100), source=new_tab)
    await _wait_for(lambda: "CloseStream" in adapter.sessions[0].controls)
    # 새 세션이 붙기를 기다리는 동안 들어온 실시간 프레임. 큐(10)를 넘는다
    for i in range(5, 30):
        stream.push(frame(i + 1, 3000 + i * 100), source=new_tab)
    await _wait_for(lambda: len(adapter.sessions) == 2 and adapter.sessions[1].audio_ms > 0)
    await asyncio.sleep(0.05)

    assert stream.dropped_audio_ms > 0
    # 마지막 프레임 끝은 Take 기준 6000ms. take_ms = base + deepgram_ms 가 성립해야 한다
    assert stream._base_offset_ms == 3000
    assert stream._base_offset_ms + adapter.sessions[1].audio_ms == 6000
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_dropped_restart_frame_still_rotates(monkeypatch: pytest.MonkeyPatch):
    """새로고침 뒤 첫 프레임(restart)이 큐가 넘쳐 드롭돼도 세션을 간다. 표시가 프레임과 함께
    사라지면 새 탭 오디오가 옛 세션에 이어 붙는다."""
    monkeypatch.setattr(take_stream, "QUEUE_MAX_FRAMES", 5)
    adapter = FakeSttAdapter()
    stream = TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    for i in range(10):
        stream.push(frame(i + 1, i * 100), source=old_tab)
        await asyncio.sleep(0)
    await _wait_for(lambda: adapter.sessions[0].audio_ms == 1000)

    # 펌프가 돌기 전에 몰려 들어와 restart 프레임(seq 1)부터 세 개가 드롭된다
    await stream.attach(new_tab)
    for i in range(8):
        stream.push(frame(i + 1, 1000 + i * 100), source=new_tab)
    await _wait_for(lambda: len(adapter.sessions) == 2 and adapter.sessions[1].audio_ms == 500)

    assert adapter.sessions[0].audio_ms == 1000, "옛 세션에 이어 붙지 않는다"
    # 살아남은 첫 프레임(seq 4, 1300ms)이 새 세션의 base 다
    assert stream._base_offset_ms == 1300
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


async def _wait_for(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except IndexError:
            pass
        await asyncio.sleep(0.01)
    raise AssertionError("시간 안에 조건이 안 됐다")


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


# ── 자동 종료 ─────────────────────────────────────────────────────────

# 테스트용으로 짧게 줄인 조건. 말 없음 300ms (창 1초 중 80%), 최대 시간은 따로 정한다
SHORT_SILENCE = AutoEndPolicy(
    silence_end_ms=300, silence_window_ms=1000, silence_ratio=0.8, max_duration_ms=3_600_000
)


def short_time_limit(ms: int) -> AutoEndPolicy:
    """최대 시간만 짧게. 서버 시계 여유도 0 으로 둬 프레임 시각과 서버 시계가 같은 값에서 끝낸다."""
    return AutoEndPolicy(
        silence_end_ms=3_600_000,
        silence_window_ms=3_600_000,
        silence_ratio=0.8,
        max_duration_ms=ms,
        server_clock_slack_ms=0,
    )


def auto_end_reason(stream: TakeStream) -> AutoEndReason | None:
    """이 프로세스가 그 Take 를 자동으로 끝냈으면 그 이유."""
    return take_stream._ended.get(stream.take_id)


def use_policy(monkeypatch: pytest.MonkeyPatch, policy: AutoEndPolicy) -> AutoEndPolicy:
    monkeypatch.setattr(AutoEndPolicy, "from_settings", classmethod(lambda cls: policy))
    return policy


def empty_final(start_ms: int, end_ms: int) -> Transcript:
    """말 없이 들은 구간. Deepgram 은 말이 없어도 빈 final 로 "여기까지 들었다" 를 알린다."""
    return Transcript(
        start_ms=start_ms,
        end_ms=end_ms,
        is_final=True,
        speech_final=False,
        from_finalize=False,
        transcript="",
        confidence=0.0,
        words=(),
    )


def receive_auto_end(ws) -> dict:
    """auto_end 가 올 때까지 읽는다 (그 사이 오는 stt_status·transcript 는 건너뛴다)."""
    while True:
        message = ws.receive_json()
        if message["type"] == "auto_end":
            return message


def make_stream(adapter: FakeSttAdapter, store: FakeTranscriptStore, policy: AutoEndPolicy):
    return TakeStream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
        auto_end=policy,
    )


def test_silence_ends_the_take_like_a_user_stop(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """말 없이 들은 구간이 쌓이면 사용자가 stop 을 보낸 것과 같은 순서로 끝낸다 —
    auto_end → 남은 오디오 정리(CloseStream) → stt_status closed → 1000 close."""
    policy = use_policy(monkeypatch, SHORT_SILENCE)
    adapter = use(
        FakeSttAdapter(
            Plan(replies=[empty_final(0, 100), empty_final(100, 200), empty_final(200, 300)])
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(3):
            ws.send_bytes(frame(i + 1, i * 100))

        ended = receive_auto_end(ws)
        status = wait_state(ws, "closed")
        assert closed_with(ws) == 1000

    assert ended == {
        "type": "auto_end",
        "reason": "SILENCE",
        "message": policy.message(AutoEndReason.SILENCE),
    }
    assert status["frames"] == 3
    assert adapter.session is not None and adapter.session.controls == ["CloseStream"]
    assert store.auto_ended == [(TAKE_ID, "SILENCE")]
    assert take_stream.active_count() == 0


def test_speech_keeps_the_take_running(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """중간에 말이 한 번이라도 있으면 말 없음은 거기서 다시 센다."""
    use_policy(monkeypatch, SHORT_SILENCE)
    use(
        FakeSttAdapter(
            Plan(
                replies=[
                    empty_final(0, 100),
                    empty_final(100, 200),
                    transcript(200, 300, "안녕", is_final=True),
                    empty_final(300, 400),
                    empty_final(400, 500),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(5):
            ws.send_bytes(frame(i + 1, i * 100))
        assert ws.receive_json()["text"] == "안녕"
        status = stop(ws)

    assert status["frames"] == 5
    assert store.auto_ended == []


def test_time_limit_by_take_offset_ends_the_take_and_keeps_the_last_frame(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """최대 시간을 넘긴 프레임에서 끝낸다. 그 프레임까지는 Deepgram 에 보낸다."""
    policy = use_policy(monkeypatch, short_time_limit(60_000))
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 59_800))
        ws.send_bytes(frame(2, 59_900))  # 60_000 에 닿는다

        ended = receive_auto_end(ws)
        wait_state(ws, "closed")
        assert closed_with(ws) == 1000

    assert ended["reason"] == "TIME_LIMIT"
    assert (
        ended["message"]
        == policy.message(AutoEndReason.TIME_LIMIT)
        == ("연습은 최대 1분까지라 자동으로 끝냈어요.")
    )
    assert adapter.session is not None and adapter.session.audio_ms == 200
    assert store.auto_ended == [(TAKE_ID, "TIME_LIMIT")]


def test_time_limit_by_server_clock_ends_a_take_whose_offset_stands_still(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """offset 은 FE 가 정한다. 프레임이 끊기거나 offset 이 멈춰 있어도 벽시계로 끝낸다."""
    use_policy(monkeypatch, short_time_limit(300))
    use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        # 더 보내지 않는다. 300ms 뒤 타이머가 끝낸다
        assert receive_auto_end(ws)["reason"] == "TIME_LIMIT"
        wait_state(ws, "closed")
        assert closed_with(ws) == 1000

    assert store.auto_ended == [(TAKE_ID, "TIME_LIMIT")]


def test_reconnect_after_auto_end_is_rejected_before_deepgram(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """DB 에 적히기 전이라도(가짜 저장소라 DB 는 그대로다) 같은 프로세스는 다시 붙이지 않는다.
    다시 붙이면 새 스트림이 열려 끝난 연습에 전사가 더 붙고 STT 비용이 다시 나간다."""
    policy = use_policy(monkeypatch, short_time_limit(1_000))
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 950))
        receive_auto_end(ws)
        wait_state(ws, "closed")

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        notice = ws.receive_json()
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    message = policy.message(AutoEndReason.TIME_LIMIT)
    # 자동 종료 순간 떨어져 있던 FE 도 auto_end 를 받아 종료 흐름을 탄다
    assert notice == {"type": "auto_end", "reason": "TIME_LIMIT", "message": message}
    assert rejected == {"type": "error", "code": "TAKE_ENDED", "message": message}
    assert len(adapter.configs) == 1, "다시 붙을 때 Deepgram 에 연결하지 않는다"


def test_auto_ended_take_in_the_database_is_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """프로세스가 다시 떠서 메모리가 비어도 DB 가 막는다 — 자동 종료가 상태를 ANALYZING 으로
    바꿔 두기 때문이다. 이유는 알림에 쓴다."""
    ended = make_take(db_session, user.id, status="ANALYZING")
    ended.end_reason = "SILENCE"
    db_session.commit()

    with client.websocket_connect(f"/api/ws/takes/{ended.id}") as ws:
        auth(ws, create_access_token(user.id))
        notice = ws.receive_json()
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    message = "2분 넘게 말이 없어 연습을 자동으로 끝냈어요."
    assert notice == {"type": "auto_end", "reason": "SILENCE", "message": message}
    assert rejected == {"type": "error", "code": "TAKE_ENDED", "message": message}
    assert stt.configs == []


def test_unknown_end_reason_in_the_database_is_still_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """이유가 나중에 늘어 이 버전이 모르는 값이어도 끝난 Take 다. FE 가 분기할 수 없으니
    auto_end 없이 일반 문구로."""
    # CHECK 를 넓힌 새 버전에서 이 버전으로 되돌린 DB 다. 테스트 트랜잭션과 함께 롤백된다
    db_session.execute(text("ALTER TABLE takes DROP CONSTRAINT check_take_end_reason"))
    ended = make_take(db_session, user.id, status="ANALYZING")
    ended.end_reason = "SOMETHING_NEW"
    db_session.commit()

    with client.websocket_connect(f"/api/ws/takes/{ended.id}") as ws:
        auth(ws, create_access_token(user.id))
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    assert rejected == {"type": "error", "code": "TAKE_ENDED", "message": "이미 끝난 연습입니다."}
    assert stt.configs == []


def test_stop_sent_after_auto_end_closes_once(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """FE 가 auto_end 를 받고 평소처럼 stop 을 보내도 된다. Deepgram 정리는 한 번이다."""
    use_policy(monkeypatch, short_time_limit(1_000))
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 950))
        receive_auto_end(ws)
        ws.send_text(json.dumps({"type": "stop"}))
        wait_state(ws, "closed")

    assert adapter.session is not None and adapter.session.controls == ["CloseStream"]
    assert take_stream.active_count() == 0


def test_end_reason_write_failure_still_closes_the_take(
    client: TestClient,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """DB 가 죽어 이유를 못 적어도 Take 는 끝낸다. 비용을 막는 게 먼저다."""
    use_policy(monkeypatch, short_time_limit(1_000))
    use_store(FakeTranscriptStore(fail_mark=True))
    use(FakeSttAdapter())
    with caplog.at_level("ERROR"), client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 950))
        receive_auto_end(ws)
        wait_state(ws, "closed")
        assert closed_with(ws) == 1000

    assert "자동 종료 이유 저장 실패" in caplog.text
    assert take_stream._ended == {TAKE_ID: AutoEndReason.TIME_LIMIT}


def test_end_reason_is_written_to_the_database_once(db_session: Session, take: Take):
    """Take 를 끝난 상태로 바꾸고 이유를 적는다. 먼저 적힌 이유가 남고, 그 사이 지워진 Take 는
    조용히 넘어간다."""
    assert take.ended_at is None
    assert take_service.mark_ended(db_session, TAKE_ID, "SILENCE") is True
    assert take_service.mark_ended(db_session, TAKE_ID, "TIME_LIMIT") is False
    db_session.refresh(take)
    assert (take.status, take.end_reason) == ("ANALYZING", "SILENCE")
    assert take.ended_at is not None, "FE 가 떨어져 있어도 끝난 시각이 남는다"
    assert take_service.mark_ended(db_session, uuid.uuid7(), "SILENCE") is False


def test_end_reason_is_not_written_to_a_take_that_already_ended(db_session: Session, user: User):
    """이미 끝난 Take(사용자가 끝내 분석 중 등)는 자동 종료로 바꾸지 않는다. FE 가 적은 끝난 시각도
    덮지 않는다."""
    ended = make_take(db_session, user.id, status="COMPLETED")
    assert take_service.mark_ended(db_session, ended.id, "TIME_LIMIT") is False
    db_session.refresh(ended)
    assert (ended.status, ended.end_reason) == ("COMPLETED", None)


@pytest.mark.anyio
async def test_db_store_marks_auto_end(db_session: Session, take: Take):
    store = DbTranscriptStore(session_factory=lambda: db_session)
    assert await store.mark_ended(TAKE_ID, "TIME_LIMIT") is True
    assert db_session.scalar(select(Take.end_reason).where(Take.id == TAKE_ID)) == "TIME_LIMIT"
    assert await store.mark_ended(TAKE_ID, "SILENCE") is False


@pytest.mark.anyio
async def test_user_stop_cancels_the_time_limit():
    """사용자가 먼저 끝냈으면 타이머가 뒤늦게 울려도 자동 종료로 바꾸지 않는다."""
    store = FakeTranscriptStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(200))
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 0))
    stream.request_stop()
    assert await stream.wait_closed(timeout=2.0)

    await asyncio.sleep(0.3)
    assert auto_end_reason(stream) is None
    assert store.auto_ended == []


@pytest.mark.anyio
async def test_time_limit_while_detached_still_ends_and_blocks_reconnect():
    """FE 가 떨어져 있는 동안(grace) 시간이 다 돼도 끝낸다. 다시 붙으면 거절한다."""
    store = FakeTranscriptStore()
    adapter = FakeSttAdapter()
    stream = make_stream(adapter, store, short_time_limit(200))
    tab = FakeWebSocket()
    stream.start()
    await stream.attach(tab)
    await _wait_until_ok(stream)
    stream.push(frame(1, 0), source=tab)
    stream.detach(tab)

    assert await stream.wait_closed(timeout=2.0)
    await _wait_for(lambda: store.auto_ended)
    assert auto_end_reason(stream) == AutoEndReason.TIME_LIMIT
    assert store.auto_ended == [(stream.take_id, "TIME_LIMIT")]
    assert not any(json.loads(t)["type"] == "auto_end" for t in tab.sent), (
        "떨어진 FE 에는 못 보낸다"
    )
    assert tab.closed is None

    with pytest.raises(take_stream.TakeEnded) as info:
        await take_stream.attach(
            stream.take_id,
            FakeWebSocket(),
            owner_id=stream.owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=store,
        )
    assert info.value.reason == AutoEndReason.TIME_LIMIT
    assert len(adapter.sessions) == 1


@pytest.mark.anyio
async def test_silence_is_not_counted_while_deepgram_is_down():
    """STT 가 죽어 있으면 전사가 안 온다. 그 시간을 말 없음으로 세면 STT 장애가 곧 강제 종료다."""
    store = FakeTranscriptStore()
    stream = make_stream(FakeSttAdapter(fail_times=1_000), store, SHORT_SILENCE)
    stream.start()
    for i in range(20):
        stream.push(frame(i + 1, i * 100))
    await asyncio.sleep(0.2)

    assert auto_end_reason(stream) is None and not stream.is_stopping
    stream.cancel()
    assert await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_silence_tracker_failure_does_not_break_the_stream(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """판단 코드에 버그가 있어도 STT 는 계속 돈다. 예외가 새면 세션이 _LOST 로 재접속한다."""

    def broken(*args, **kwargs):
        raise ValueError("tracker bug")

    monkeypatch.setattr(SilenceTracker, "observe", broken)
    store = FakeTranscriptStore()
    adapter = FakeSttAdapter(Plan(replies=[transcript(0, 100, "하나", is_final=True)]))
    stream = make_stream(adapter, store, SHORT_SILENCE)
    stream.start()
    await _wait_until_ok(stream)

    with caplog.at_level("ERROR"):
        stream.push(frame(1, 0))
        await _wait_for(lambda: store.segments)

    assert "말 없음 판단 실패" in caplog.text
    assert len(adapter.sessions) == 1 and stream.state == "ok"
    stream.request_stop()
    assert await stream.wait_closed(timeout=2.0)
    assert auto_end_reason(stream) is None


@pytest.mark.anyio
async def test_unexpected_error_in_auto_end_cleanup_is_logged_with_the_take(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """정리 태스크는 아무도 await 하지 않는다. 예외가 나면 어느 Take 인지 로그에 남겨야 한다."""

    async def broken(self, reason):
        raise RuntimeError("cleanup bug")

    monkeypatch.setattr(TakeStream, "_finish_auto_end", broken)
    stream = make_stream(FakeSttAdapter(), FakeTranscriptStore(), short_time_limit(1_000))
    stream.start()
    await _wait_until_ok(stream)

    with caplog.at_level("ERROR"):
        stream.push(frame(1, 950))
        assert await stream.wait_closed(timeout=2.0), "정리 태스크가 죽어도 스트림은 닫힌다"
        await _wait_for(lambda: "백그라운드 태스크 실패" in caplog.text)

    assert f"take-auto-end:{stream.take_id}" in caplog.text


@pytest.mark.anyio
async def test_time_limit_ends_a_connected_take_that_never_sends_audio():
    """오디오 없이 연결만 붙여 둔 Take 도 끝낸다. 첫 프레임에서 타이머를 걸면 영영 안 걸리고
    Deepgram 연결이 KeepAlive 로 계속 살아 있다."""
    store = FakeTranscriptStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(200))
    tab = FakeWebSocket()
    stream.start()
    await stream.attach(tab)

    assert await stream.wait_closed(timeout=2.0)
    await _wait_for(lambda: tab.closed is not None)
    assert auto_end_reason(stream) == AutoEndReason.TIME_LIMIT
    assert tab.closed == (1000, "")
    assert [json.loads(t)["type"] for t in tab.sent][-2:] == ["auto_end", "stt_status"]


@pytest.mark.anyio
async def test_server_clock_is_per_take_not_per_stream():
    """grace 보다 오래 끊었다 새 스트림으로 다시 붙어도 1시간이 새로 시작하지 않는다.
    스트림 기준이면 30초 넘게 끊었다 붙기를 반복해 제한을 계속 피할 수 있다."""
    take_id = uuid.uuid7()
    policy = short_time_limit(400)

    def make() -> TakeStream:
        return TakeStream(
            take_id,
            owner_id=uuid.uuid7(),
            stt_adapter=FakeSttAdapter(),
            config=SttConfig(),
            store=FakeTranscriptStore(),
            auto_end=policy,
        )

    first = make()
    first.start()
    await asyncio.sleep(0.25)
    first.request_stop()  # grace 가 지나 사라진 것과 같다
    assert await first.wait_closed(timeout=2.0)
    assert auto_end_reason(first) is None

    second = make()
    second.start()
    started = time.monotonic()
    assert await second.wait_closed(timeout=2.0)
    assert auto_end_reason(second) == AutoEndReason.TIME_LIMIT
    assert time.monotonic() - started < 0.3, "남은 시간(약 150ms)만 기다린다"


@pytest.mark.anyio
async def test_shutdown_waits_for_the_end_reason_to_be_written():
    """앱이 내려갈 때 자동 종료 정리를 끊으면 이유가 DB 에 안 남아 다시 뜬 뒤 재연결을 못 막는다."""

    class SlowStore(FakeTranscriptStore):
        async def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
            await asyncio.sleep(0.2)
            return await super().mark_ended(take_id, end_reason)

    store = SlowStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(1_000))
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 950))

    await take_stream.stop_all()

    assert store.auto_ended == [(stream.take_id, "TIME_LIMIT")]


@pytest.mark.anyio
async def test_server_clock_does_not_count_time_without_a_stream():
    """탭이 죽어 스트림이 없던 시간은 세지 않는다. 벽시계로 재면 한참 뒤 이어서 연습하자마자
    끝나 버린다 (무대 시계는 끊긴 동안 멈춰 있다)."""
    take_id = uuid.uuid7()
    policy = short_time_limit(400)

    def make() -> TakeStream:
        return TakeStream(
            take_id,
            owner_id=uuid.uuid7(),
            stt_adapter=FakeSttAdapter(),
            config=SttConfig(),
            store=FakeTranscriptStore(),
            auto_end=policy,
        )

    first = make()
    first.start()
    await asyncio.sleep(0.1)
    first.request_stop()
    assert await first.wait_closed(timeout=2.0)

    await asyncio.sleep(0.4)  # 스트림 없이 최대 시간이 지난다

    second = make()
    second.start()
    await asyncio.sleep(0.15)
    assert not second.is_stopping, "열려 있던 시간은 아직 0.25초쯤이다"
    assert await second.wait_closed(timeout=2.0)
    assert auto_end_reason(second) == AutoEndReason.TIME_LIMIT


@pytest.mark.anyio
async def test_server_clock_waits_for_the_slack_so_the_frame_time_ends_first():
    """서버 시계는 보조 상한이라 여유만큼 늦게 끝낸다. 정상 Take 는 프레임 시각이 먼저 끝낸다."""
    policy = AutoEndPolicy(
        silence_end_ms=3_600_000,
        silence_window_ms=3_600_000,
        silence_ratio=0.8,
        max_duration_ms=100,
        server_clock_slack_ms=300,
    )
    stream = make_stream(FakeSttAdapter(), FakeTranscriptStore(), policy)
    stream.start()
    await asyncio.sleep(0.2)
    assert not stream.is_stopping
    assert await stream.wait_closed(timeout=2.0)
    assert auto_end_reason(stream) == AutoEndReason.TIME_LIMIT


@pytest.mark.anyio
async def test_silence_filled_by_the_server_is_not_counted():
    """프레임 사이 틈은 BE 가 무음으로 메워 Deepgram 에 보낸다. 그 구간의 빈 final 은 들은 게
    아니라 말 없음으로 세지 않는다 — 세면 네트워크가 흔들릴 때 말하는 사람도 끊긴다."""
    store = FakeTranscriptStore()
    adapter = FakeSttAdapter(
        Plan(replies=[empty_final(0, 100), empty_final(100, 400), empty_final(400, 500)])
    )
    stream = make_stream(adapter, store, SHORT_SILENCE)
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 0))
    stream.push(frame(2, 400))  # 300ms 틈 → 무음으로 메움

    await _wait_for(lambda: adapter.session is not None and adapter.session.audio_ms == 500)
    await asyncio.sleep(0.05)
    # 들은 말 없음은 200ms 뿐이다 (SHORT_SILENCE 는 300ms 에서 끝낸다)
    assert auto_end_reason(stream) is None and not stream.is_stopping
    stream.request_stop()
    assert await stream.wait_closed(timeout=2.0)


@pytest.mark.anyio
async def test_silence_carries_over_to_a_new_stream_of_the_same_take():
    """말 없음 기록은 Take 에 붙어 있다. 30초 넘게 끊겼다 새 스트림으로 붙기를 반복해도
    처음부터 다시 세지 않는다."""
    take_id = uuid.uuid7()

    def make(*replies: Transcript) -> tuple[TakeStream, FakeSttAdapter]:
        adapter = FakeSttAdapter(Plan(replies=list(replies)))
        stream = TakeStream(
            take_id,
            owner_id=uuid.uuid7(),
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
            auto_end=SHORT_SILENCE,
        )
        return stream, adapter

    first, adapter = make(empty_final(0, 100), empty_final(100, 200))
    first.start()
    await _wait_until_ok(first)
    first.push(frame(1, 0))
    first.push(frame(2, 100))
    await _wait_for(lambda: adapter.session is not None and adapter.session.audio_ms == 200)
    await asyncio.sleep(0.05)
    first.request_stop()  # grace 가 지나 사라진 것과 같다
    assert await first.wait_closed(timeout=2.0)
    assert auto_end_reason(first) is None

    second, _ = make(empty_final(0, 100))
    second.start()
    await _wait_until_ok(second)
    second.push(frame(1, 200))
    # 앞 스트림의 200ms + 이번 100ms = 300ms
    assert await second.wait_closed(timeout=2.0)
    assert auto_end_reason(second) == AutoEndReason.SILENCE


@pytest.mark.anyio
async def test_auto_end_on_a_take_that_already_ended_is_logged(caplog: pytest.LogCaptureFixture):
    """DB 에 못 적었다(Take 가 지워졌거나 이미 끝남)는 것을 조용히 넘기지 않는다."""
    stream = make_stream(
        FakeSttAdapter(), FakeTranscriptStore(marks=False), short_time_limit(1_000)
    )
    stream.start()
    await _wait_until_ok(stream)

    with caplog.at_level("WARNING"):
        stream.push(frame(1, 950))
        assert await stream.wait_closed(timeout=2.0)
        await _wait_for(lambda: "자동 종료를 적지 않았다" in caplog.text)


@pytest.mark.anyio
async def test_shutdown_cancels_cleanup_that_does_not_finish_and_says_which(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """정리가 상한 안에 안 끝나면 루프가 닫히며 조용히 사라지게 두지 않고, 무엇을 끊는지 남긴다."""

    class StuckStore(FakeTranscriptStore):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()

        async def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
            self.entered.set()
            await asyncio.Event().wait()
            return True

    monkeypatch.setattr(take_stream, "MARK_AUTO_END_TIMEOUT_SEC", 10.0)
    monkeypatch.setattr(take_stream, "ABORT_TIMEOUT_SEC", 0.1)
    store = StuckStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(1_000))
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 950))
    # 스트림을 정리하고 DB 쓰기(상한 10초)에 들어갈 때까지 기다린다
    await asyncio.wait_for(store.entered.wait(), 2.0)

    # 상한(10.1초)을 기다리지 않게 줄인다. 스트림 정리가 끝난 뒤의 대기만 본다
    monkeypatch.setattr(take_stream, "MARK_AUTO_END_TIMEOUT_SEC", 0.1)
    with caplog.at_level("ERROR"):
        await take_stream.stop_all()

    assert f"take-auto-end:{stream.take_id}" in caplog.text
    assert not take_stream._background


# ── 떠난 Take ─────────────────────────────────────────────────────────


def make_plain_stream(take_id: uuid.UUID, store: FakeTranscriptStore) -> TakeStream:
    return TakeStream(
        take_id,
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )


async def open_and_leave(stream: TakeStream) -> None:
    """레지스트리에 올리고 오디오를 한 번 보낸 뒤 탭이 사라진다. grace 가 지나면 스트림만 닫힌다."""
    tab = FakeWebSocket()
    take_stream._streams[stream.take_id] = stream
    stream.start()
    await stream.attach(tab)
    await _wait_until_ok(stream)
    stream.push(frame(1, 0), source=tab)
    stream.detach(tab)
    assert await stream.wait_closed(timeout=2.0)


@pytest.mark.anyio
async def test_take_left_without_reconnect_is_ended_as_abandoned(monkeypatch: pytest.MonkeyPatch):
    """탭을 닫으면 grace 뒤 스트림만 닫히고 Take 는 진행 중으로 남는다. 정해진 시간 동안 아무도 다시
    붙지 않으면 떠난 것으로 끝낸다. 끝난 시각은 끝내는 지금이 아니라 연결이 끊긴 시각이다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    monkeypatch.setattr(take_stream, "ABANDON_AFTER_SEC", 0.2)
    store = FakeTranscriptStore()
    stream = make_plain_stream(uuid.uuid7(), store)

    await open_and_leave(stream)
    closed_at = datetime.now(UTC)
    assert store.abandoned == [], "grace 만료는 스트림만 정리한다"

    await _wait_for(lambda: store.abandoned)
    [(take_id, ended_at)] = store.abandoned
    assert take_id == stream.take_id
    assert abs((ended_at - closed_at).total_seconds()) < 0.1
    assert store.calls == ["mark_abandoned"]

    # 그 뒤로 다시 붙으면 거절한다. 자동 종료가 아니라 이유 없이
    with pytest.raises(take_stream.TakeEnded) as info:
        await take_stream.attach(
            stream.take_id,
            FakeWebSocket(),
            owner_id=stream.owner_id,
            stt_adapter=FakeSttAdapter(),
            config=SttConfig(),
            store=store,
        )
    assert info.value.reason is None


@pytest.mark.anyio
async def test_reconnect_before_the_wait_is_over_keeps_the_take(monkeypatch: pytest.MonkeyPatch):
    """그 안에 다시 붙으면(탭이 살아 있던 FE, 리허설 주소를 다시 연 사용자) 이어서 연습한다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    monkeypatch.setattr(take_stream, "ABANDON_AFTER_SEC", 0.2)
    store = FakeTranscriptStore()
    stream = make_plain_stream(uuid.uuid7(), store)
    await open_and_leave(stream)

    resumed = await take_stream.attach(
        stream.take_id,
        FakeWebSocket(),
        owner_id=stream.owner_id,
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )
    assert resumed is not stream
    assert stream.take_id not in take_stream._abandon_timers

    await asyncio.sleep(0.3)
    assert store.abandoned == []
    assert stream.take_id not in take_stream._ended


@pytest.mark.anyio
async def test_ended_or_deleted_take_is_not_waited_for():
    """사용자가 끝냈거나(stop · 자동 종료 · Take PUT) Take 가 지워졌으면 기다릴 것이 없다."""
    store = FakeTranscriptStore()

    stopped = make_plain_stream(uuid.uuid7(), store)
    stopped.start()
    await _wait_until_ok(stopped)
    stopped.end()
    assert await stopped.wait_closed(timeout=2.0)

    deleted = make_plain_stream(uuid.uuid7(), store)
    deleted.start()
    await _wait_until_ok(deleted)
    deleted.discard()
    assert await deleted.wait_closed(timeout=2.0)

    assert take_stream._abandon_timers == {}

    # 떠난 뒤(스트림이 닫혀 타이머만 남음) 지운 Take
    left = uuid.uuid7()
    take_stream.arm_abandon(left, store, closed_at=datetime.now(UTC))
    await take_stream.discard([left])
    assert take_stream._abandon_timers == {}


@pytest.mark.anyio
async def test_timer_that_fires_while_a_stream_is_open_does_not_end_the_take():
    """옛 스트림의 정리가 새 스트림이 열린 뒤에 끝나 타이머가 남을 수 있다. 울릴 때 다시 본다."""
    store = FakeTranscriptStore()
    stream = make_plain_stream(uuid.uuid7(), store)
    take_stream._streams[stream.take_id] = stream
    stream.start()
    await _wait_until_ok(stream)

    take_stream.arm_abandon(stream.take_id, store, closed_at=datetime.now(UTC), delay_sec=0)
    await asyncio.sleep(0.1)

    assert store.abandoned == []
    assert not stream.is_stopping


@pytest.mark.anyio
async def test_abandon_write_failure_leaves_the_take_open(caplog: pytest.LogCaptureFixture):
    """DB 에 못 적었으면 이 프로세스에서도 끝난 것으로 치지 않는다. 다음에 뜰 때 다시 건다."""
    store = FakeTranscriptStore(fail_mark=True)
    take_id = uuid.uuid7()

    with caplog.at_level("ERROR"):
        take_stream.arm_abandon(take_id, store, closed_at=datetime.now(UTC), delay_sec=0)
        await _wait_for(lambda: "떠난 Take 를 끝내지 못했다" in caplog.text)

    assert take_id not in take_stream._ended


@pytest.mark.anyio
async def test_startup_rearms_timers_from_the_last_activity(monkeypatch: pytest.MonkeyPatch):
    """앱이 다시 뜨면 메모리의 타이머가 없다. 진행 중으로 남은 Take 를 DB 에서 읽어 마지막 활동에서
    잰 남은 시간으로 다시 건다. 이미 지난 Take 는 바로 끝낸다."""
    monkeypatch.setattr(take_stream, "ABANDON_AFTER_SEC", 60.0)
    store = FakeTranscriptStore()
    now = datetime.now(UTC)
    stale, recent = uuid.uuid7(), uuid.uuid7()
    store.running = [(stale, now - timedelta(seconds=61)), (recent, now - timedelta(seconds=20))]

    await take_stream.arm_abandoned_takes(store)

    await _wait_for(lambda: store.abandoned)
    assert store.abandoned == [(stale, now - timedelta(seconds=61))]
    remaining = take_stream._abandon_timers[recent].when() - asyncio.get_running_loop().time()
    assert 39 < remaining <= 40


@pytest.mark.anyio
async def test_startup_does_not_fail_when_the_running_takes_cannot_be_read(
    caplog: pytest.LogCaptureFixture,
):
    class BrokenStore(FakeTranscriptStore):
        async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
            raise RuntimeError("db down")

    with caplog.at_level("ERROR"):
        await take_stream.arm_abandoned_takes(BrokenStore())

    assert "떠난 Take 타이머를 걸지 못했다" in caplog.text
    assert take_stream._abandon_timers == {}


@pytest.mark.anyio
async def test_startup_does_not_wait_for_a_slow_database(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """진행 중인 Take 를 읽는 동안 앱이 요청을 받지 못한다. 상한을 넘기면 기다리지 않고 뜬다."""
    monkeypatch.setattr(take_stream, "ARM_ABANDONED_TIMEOUT_SEC", 0.05)

    class SlowStore(FakeTranscriptStore):
        async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
            await asyncio.sleep(10)
            return [(uuid.uuid7(), datetime.now(UTC))]

    with caplog.at_level("ERROR"):
        await asyncio.wait_for(take_stream.arm_abandoned_takes(SlowStore()), 1)

    assert "떠난 Take 타이머를 걸지 못했다" in caplog.text
    assert take_stream._abandon_timers == {}


def test_abandoned_take_is_failed_from_its_close_time(db_session: Session, take: Take):
    """떠난 Take 는 분석하지 않는다 (FAILED). 진행 중일 때만 바꾸고, 지워진 Take 는 넘어간다."""
    closed_at = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert take_service.mark_abandoned(db_session, TAKE_ID, closed_at) is True
    assert take_service.mark_abandoned(db_session, TAKE_ID, datetime.now(UTC)) is False
    db_session.refresh(take)
    assert (take.status, take.end_reason, take.ended_at) == ("FAILED", "ABANDONED", closed_at)
    assert take_service.mark_abandoned(db_session, uuid.uuid7(), closed_at) is False


def test_abandon_does_not_touch_a_take_that_already_ended(db_session: Session, user: User):
    ended = make_take(db_session, user.id, status="ANALYZING")
    assert take_service.mark_abandoned(db_session, ended.id, datetime.now(UTC)) is False
    db_session.refresh(ended)
    assert (ended.status, ended.end_reason) == ("ANALYZING", None)


def test_running_takes_report_their_last_activity(db_session: Session, user: User):
    """만든 시각 · 시작 시각 · 마지막 전사를 저장한 시각 중 가장 늦은 것. 진행 중인 Take 만."""
    created = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    idle = make_take(db_session, user.id)
    started = make_take(db_session, user.id)
    spoken = make_take(db_session, user.id)
    ended = make_take(db_session, user.id, status="ANALYZING")
    for t in (idle, started, spoken, ended):
        t.created_at = created
    started.started_at = created + timedelta(minutes=1)
    spoken.started_at = created + timedelta(minutes=1)
    db_session.add_all(
        TakeTranscriptSegment(
            take_id=spoken.id,
            seq=seq,
            stt_session_no=1,
            start_ms=0,
            end_ms=100,
            transcript="말",
            words=[],
            confidence=0.9,
            speech_final=True,
            created_at=created + timedelta(minutes=minutes),
        )
        for seq, minutes in ((1, 2), (2, 5))
    )
    db_session.commit()

    running = dict(take_service.running_takes(db_session))

    assert running == {
        idle.id: created,
        started.id: created + timedelta(minutes=1),
        spoken.id: created + timedelta(minutes=5),
    }


@pytest.mark.anyio
async def test_db_store_marks_abandoned(db_session: Session, take: Take):
    store = DbTranscriptStore(session_factory=lambda: db_session)
    assert await store.running_takes() == [(TAKE_ID, take.created_at)]
    assert await store.mark_abandoned(TAKE_ID, datetime.now(UTC)) is True
    assert db_session.scalar(select(Take.status).where(Take.id == TAKE_ID)) == "FAILED"
    assert await store.running_takes() == []


def test_abandoned_take_in_the_database_is_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """프로세스가 다시 떠도 DB 가 막는다. 자동 종료가 아니라 auto_end 없이 일반 문구로."""
    left = make_take(db_session, user.id, status="FAILED")
    left.end_reason = "ABANDONED"
    db_session.commit()

    with client.websocket_connect(f"/api/ws/takes/{left.id}") as ws:
        auth(ws, create_access_token(user.id))
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    assert rejected == {"type": "error", "code": "TAKE_ENDED", "message": "이미 끝난 연습입니다."}
    assert stt.configs == []
