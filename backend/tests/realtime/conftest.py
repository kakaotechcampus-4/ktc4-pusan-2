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
from datetime import datetime

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosedError

from pitch_coach_backend.coach.requests import SlideEvent
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.main import app
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptVersion,
)
from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO, TranscriptSegmentCreateDTO
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime import dependencies as realtime_dependencies
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.dependencies import get_stt_adapter
from pitch_coach_backend.realtime.stream import abandon, finish, registry, runtime, state
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    Metadata,
    SttConfig,
    SttConfigRejected,
    SttConnectError,
    SttEvent,
    Transcript,
    Word,
)

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
        metric_errors: Iterable[Exception] = (),
        stored_utterance_ends: dict[uuid.UUID, list[int]] | None = None,
    ) -> None:
        self.segments: list[TranscriptSegmentCreateDTO] = []
        # 앞 프로세스가 적어 둔 문장 끝 신호 (Take → 목록)와 앱을 내릴 때 적은 것
        self.stored_utterance_ends = dict(stored_utterance_ends or {})
        self.saved_utterance_ends: dict[uuid.UUID, list[int]] = {}
        # 저장한 1초 원자료. DB 처럼 같은 1초는 먼저 저장한 것을 둔다
        self.metrics: dict[int, MetricSecondCreateDTO] = {}
        # 저장한 장 변경. 같은 시각은 먼저 저장한 것을 둔다
        self.slides: dict[int, SlideEvent] = {}
        # 끝낼 때 같이 적은 문장 끝 신호 (Take → 목록)
        self.utterance_ends: dict[uuid.UUID, list[int] | None] = {}
        self.take_ids: list[uuid.UUID] = []
        self.ended: list[uuid.UUID] = []
        # 자동 종료로 적은 (Take, 이유)
        self.auto_ended: list[tuple[uuid.UUID, str]] = []
        # 떠난 것으로 적은 (Take, 끝난 시각)과 그 이유 (ABANDONED · CLOSED)
        self.abandoned: list[tuple[uuid.UUID, datetime]] = []
        self.abandon_reasons: list[str] = []
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
        # 1초 원자료를 저장할 때마다 앞에서 하나씩 꺼내 던진다. 다 쓰면 정상으로 저장한다
        self.metric_errors = list(metric_errors)

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        self.cursor_reads.append(len(self.segments))
        return self._cursor

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await asyncio.sleep(self.append_delay)  # 실제 저장은 threadpool 왕복이라 한 번은 양보한다
        if self.fail:
            raise db_down()
        if self.errors:
            raise self.errors.pop(0)
        self.take_ids.append(take_id)
        self.segments.append(segment)
        self.calls.append("append")

    async def append_metrics(self, take_id: uuid.UUID, rows: list[MetricSecondCreateDTO]) -> None:
        await asyncio.sleep(self.append_delay)
        if self.metric_errors:
            raise self.metric_errors.pop(0)
        for row in rows:
            self.metrics.setdefault(row.t_ms, row)
        self.calls.append("append_metrics")

    async def append_slide_events(self, take_id: uuid.UUID, rows: list[SlideEvent]) -> None:
        await asyncio.sleep(self.append_delay)
        for row in rows:
            self.slides.setdefault(row.started_ms, row)
        self.calls.append("append_slide_events")

    async def mark_ended(
        self,
        take_id: uuid.UUID,
        end_reason: str | None = None,
        utterance_ends: list[int] | None = None,
    ) -> bool:
        await asyncio.sleep(0)
        if self.fail_mark:
            raise db_down()
        # DB 처럼 이미 끝난 Take 는 다시 적지 않는다
        if not self.marks or take_id in self.ended:
            return False
        self.ended.append(take_id)
        self.utterance_ends[take_id] = utterance_ends
        self.calls.append("mark_ended")
        if end_reason is not None:
            self.auto_ended.append((take_id, end_reason))
        return True

    async def mark_abandoned(
        self,
        take_id: uuid.UUID,
        ended_at: datetime,
        end_reason: str = "ABANDONED",
        utterance_ends: list[int] | None = None,
    ) -> bool:
        await asyncio.sleep(0)
        if self.fail_mark:
            raise db_down()
        if not self.marks or take_id in self.ended:
            return False
        self.ended.append(take_id)
        self.utterance_ends[take_id] = utterance_ends
        self.calls.append("mark_abandoned")
        self.abandoned.append((take_id, ended_at))
        self.abandon_reasons.append(end_reason)
        return True

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        return list(self.running)

    async def load_utterance_ends(self, take_id: uuid.UUID) -> list[int]:
        return list(self.stored_utterance_ends.get(take_id, []))

    async def save_utterance_ends(self, take_id: uuid.UUID, utterance_ends: list[int]) -> None:
        self.saved_utterance_ends[take_id] = list(utterance_ends)


def db_down() -> OperationalError:
    """DB 가 잠깐 안 되는 것 (연결 끊김). 뒤에서 다시 해 볼 만한 실패다."""
    return OperationalError("UPDATE takes", {}, ConnectionError("db down"))


@pytest.fixture(autouse=True)
def _no_leftover_streams():
    """스트림은 연결보다 오래 산다. 테스트 사이에 넘어가면 죽은 이벤트 루프의 태스크가 남는다."""
    yield
    for stream in list(state.streams.values()):
        stream.cancel()
    state.streams.clear()
    state.attach_locks.clear()
    state.ended.clear()
    state.takes.clear()
    for timer in abandon._abandon_timers.values():
        timer.cancel()
    abandon._abandon_timers.clear()
    app.dependency_overrides.pop(get_stt_adapter, None)


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeTranscriptStore:
    """기본 저장소는 가짜다. 실제 DbTranscriptStore 는 SessionLocal 로 새 커넥션을 열어서
    테스트 트랜잭션 안의(아직 커밋 안 된) Take 행을 못 보고 FK 위반이 난다.

    프로세스에 하나인 저장소를 바꿔 끼운다 — 끝내기 PUT(module/hooks.py 의 구현)도 같은 것을 꺼내
    쓴다. 테스트가 끝나면 원래 것으로 돌아간다."""
    fake = FakeTranscriptStore()
    monkeypatch.setattr(realtime_dependencies, "_transcript_store", fake)
    return fake


def use_store(fake: FakeTranscriptStore) -> FakeTranscriptStore:
    # 위 fixture 가 원래 것을 기억해 두었다가 테스트가 끝나면 되돌린다
    realtime_dependencies._transcript_store = fake
    return fake


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(stt_loop, "BACKOFF_SEC", (0.01,))
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 2.0)
    # 자동 종료 뒤 stop 을 보내지 않는 테스트가 기다리지 않게
    monkeypatch.setattr(runtime, "FINAL_METRICS_WAIT_SEC", 0.05)
    monkeypatch.setattr(finish, "MARK_RETRY_SEC", (0.01,))


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(email="ws@example.com", name="WS")
    db_session.add(user)
    # 서비스가 인증 뒤 rollback() 으로 커넥션을 돌려준다. 커밋(=savepoint 해제) 해 둬야 살아남는다
    db_session.commit()
    return user


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
        # Starlette 의 `WebSocket.client`. 소켓 상대 주소이고 모르면 None 이다
        self.client = None
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


async def _wait_until_ok(stream: TakeStream, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while stream.state != "ok" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert stream.state == "ok", f"연결이 ok 상태가 되지 않았다 (state={stream.state})"


def wait_until(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition()


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
    return registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
        auto_end=policy,
    )


def metrics(t_ms: int, *, slide_number: int | None = 1, duration_ms: int = 1000) -> dict:
    """FE 가 보내는 1초 측정값. 바깥 t_ms 는 그 1초가 끝난 시각, 안쪽 t_ms 는 시작 시각이다."""
    start = t_ms - duration_ms
    return {
        "type": "metrics",
        "t_ms": t_ms,
        "gaze": {"t_ms": start, "duration_ms": duration_ms, "state": "BOTTOM", "confidence": 0.83},
        "voice": {"t_ms": start, "duration_ms": duration_ms, "level_db": -31.2, "audio_live": True},
        "slide_number": slide_number,
    }


def send_metrics(ws, *t_ms: int, slide_number: int | None = 1) -> None:
    for t in t_ms:
        ws.send_text(json.dumps(metrics(t, slide_number=slide_number)))


def row(t_ms: int, *, slide_number: int | None = 1) -> MetricSecondCreateDTO:
    return MetricSecondCreateDTO(
        t_ms=t_ms, slide_number=slide_number, gaze=None, voice=None, stt_status="ok"
    )
