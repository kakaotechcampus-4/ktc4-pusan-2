"""확정 전사 — 저장 · 번호 이어받기 · 재전송 · Take 삭제 시 끊기."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO
from pitch_coach_backend.module.take.entity import Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import DuplicateTranscriptSegment, NonExistentTake
from pitch_coach_backend.module.take.service import record as record_service
from pitch_coach_backend.realtime.connection import session
from pitch_coach_backend.realtime.dto import (
    TranscriptMessage,
)
from pitch_coach_backend.realtime.storage import transcripts as transcripts_module
from pitch_coach_backend.realtime.storage.store import DbTranscriptStore
from pitch_coach_backend.realtime.storage.transcripts import TranscriptBuffer
from pitch_coach_backend.realtime.stream import registry
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttConfig,
)
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    _wait_for,
    _wait_until_ok,
    closed_with,
    frame,
    handshake,
    stop,
    transcript,
    use,
    use_store,
    wait_state,
    wait_until,
)

# ── 떨어져 있는 동안 온 final ─────────────────────────────────────────────────


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


# ── 최소 WebSocket 더블 ──────────────────────────────────────────────


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


# ── final 재전송이 실패하면 나머지도 같이 사라지는 문제 ─────────────────


@pytest.mark.anyio
async def test_resend_missed_keeps_unsent_finals_when_send_fails_partway():
    """한꺼번에 비우고 보내면, 두 번째 전송이 실패했을 때 아직 못 보낸 세 번째 이후가
    이미 비워진 리스트와 함께 사라진다. 성공한 만큼만 지워야 다음 재연결 때
    나머지를 다시 시도할 수 있다."""
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream._transcripts.missed_finals = [
        transcript_message("1-1", "첫"),
        transcript_message("1-2", "둘"),
        transcript_message("1-3", "셋"),
    ]
    fake_ws = FakeWebSocket(fail_send_after=2)  # 두 번째 전송에서 끊긴다
    await stream.attach(fake_ws)

    await stream.resend_missed()

    assert [json.loads(t)["text"] for t in fake_ws.sent] == ["첫"]
    assert [m.text for m in stream._transcripts.missed_finals] == ["둘", "셋"], (
        "실패한 지점부터는 다음 재연결을 위해 남아 있어야 한다"
    )


# ── 핸드오버 타임아웃 시 옛 스트림이 정리 안 되는 문제 ──────────────────


@pytest.mark.anyio
async def test_finals_received_before_deepgram_closes_are_saved():
    """Deepgram 이 먼저 끊으면 오디오 쪽이 그걸 먼저 알 수 있다. 그때 이벤트 쪽을 바로 끊으면
    이미 받아 둔 final 이 화면에도 DB 에도 남지 않는다. 이벤트 쪽이 남은 것을 다 읽게 둔다."""
    adapter = FakeSttAdapter(
        Plan(replies=[transcript(0, 200, "끊기 전 말", is_final=True)], die_after=1),
        Plan(),
    )
    store = FakeTranscriptStore()
    stream = registry.create_stream(
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
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream._transcripts.missed_finals = [
        transcript_message("1-1", "첫"),
        transcript_message("1-2", "둘"),
    ]
    ws_a = FakeWebSocket()
    await stream.attach(ws_a)

    # 두 호출이 서로 await 지점에서 번갈아 실행되도록(진짜 동시 호출처럼) gather 로 묶는다.
    # IndexError 가 나면 gather 자체가 그 예외로 실패한다
    await asyncio.gather(stream.resend_missed(), stream.resend_missed())

    assert stream._transcripts.missed_finals == []
    # 두 메시지 모두 (중복 없이) 정확히 한 번씩만 나갔다
    sent_texts = [json.loads(t)["text"] for t in ws_a.sent]
    assert sent_texts == ["첫", "둘"]


# ── 보내다 실패한 final ────────────────────────────────────────────────


class StuckWebSocket(FakeWebSocket):
    """`stuck` 인 동안 보내기가 `release` 될 때까지 멈춘다. 상대가 받지 않는 연결이다."""

    def __init__(self) -> None:
        super().__init__()
        self.stuck = True
        self.release = asyncio.Event()
        self.sending = asyncio.Event()

    async def send_text(self, text: str) -> None:
        if self.stuck:
            self.sending.set()
            await self.release.wait()
        await super().send_text(text)


def _final_stream(store: FakeTranscriptStore):
    return registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )


def _texts(ws: FakeWebSocket) -> list[tuple[str, str]]:
    return [(m["segment_id"], m["text"]) for m in map(json.loads, ws.sent)]


@pytest.mark.anyio
async def test_final_that_fails_to_send_is_resent_on_reconnect():
    """붙어 있는 연결로 보내다 실패한 final 도 떨어져 있던 동안 온 것처럼 모은다. 보낸 셈 치면
    다시 붙은 화면에서 그 구간이 빈다. 저장은 화면과 따로 한다."""
    store = FakeTranscriptStore()
    stream = _final_stream(store)
    dead = FakeWebSocket(fail_send_after=1)
    await stream.attach(dead)

    await stream._on_transcript(transcript(0, 200, "끊긴 말", is_final=True))

    assert [seg.transcript for seg in store.segments] == ["끊긴 말"]
    stream.detach(dead)
    fresh = FakeWebSocket()
    await stream.attach(fresh)
    await stream.resend_missed()
    assert _texts(fresh) == [("0-1", "끊긴 말")]
    assert stream._transcripts.missed_finals == []


@pytest.mark.anyio
async def test_final_that_times_out_is_resent_on_reconnect(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 0.05)
    store = FakeTranscriptStore()
    stream = _final_stream(store)
    stuck = StuckWebSocket()
    await stream.attach(stuck)

    await stream._on_transcript(transcript(0, 200, "멈춘 말", is_final=True))

    assert [seg.transcript for seg in store.segments] == ["멈춘 말"]
    assert [m.segment_id for m in stream._transcripts.missed_finals] == ["0-1"]
    stream.detach(stuck)
    fresh = FakeWebSocket()
    await stream.attach(fresh)
    await stream.resend_missed()
    assert _texts(fresh) == [("0-1", "멈춘 말")]


@pytest.mark.anyio
async def test_final_sent_normally_is_not_resent():
    stream = _final_stream(FakeTranscriptStore())
    ws = FakeWebSocket()
    await stream.attach(ws)

    await stream._on_transcript(transcript(0, 200, "보낸 말", is_final=True))
    await stream.resend_missed()

    assert _texts(ws) == [("0-1", "보낸 말")]
    assert stream._transcripts.missed_finals == []


@pytest.mark.anyio
async def test_final_that_failed_while_another_tab_caught_up_goes_to_that_tab(
    monkeypatch: pytest.MonkeyPatch,
):
    """옛 연결로 보내는 동안 다른 탭이 이어받아 놓친 final 을 이미 받았다. 실패한 final 을 다음
    재연결까지 들고 있으면 지금 탭의 화면에서 그 구간이 빈다."""
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 0.2)
    store = FakeTranscriptStore()
    stream = _final_stream(store)
    old_tab = StuckWebSocket()
    await stream.attach(old_tab)
    showing = asyncio.create_task(
        stream._on_transcript(transcript(0, 200, "겹친 말", is_final=True))
    )
    await old_tab.sending.wait()

    # 옛 연결은 이제 무엇을 보내도 실패한다. 쫓겨나며 받는 알림도 바로 실패한다
    old_tab.stuck = False
    old_tab.fail_send_after = 1
    new_tab = FakeWebSocket()
    await stream.attach(new_tab)
    await stream.resend_missed()
    assert new_tab.sent == []
    # 새 탭이 따라잡은 뒤에야 옛 연결로 보내던 final 이 실패한다
    old_tab.release.set()
    await showing

    assert _texts(new_tab) == [("0-1", "겹친 말")]
    assert stream._transcripts.missed_finals == []
    assert [seg.transcript for seg in store.segments] == ["겹친 말"]


@pytest.mark.anyio
async def test_final_that_failed_before_the_new_tab_is_ready_waits_for_its_catch_up():
    """이어받은 연결이 아직 ready 전이면 지금 보내지 않는다. 그 연결이 따라잡을 때 같이 간다."""
    stream = _final_stream(FakeTranscriptStore())
    old_tab = FakeWebSocket(fail_send_after=1)
    await stream.attach(old_tab)
    await stream.resend_missed()

    new_tab = FakeWebSocket()
    sending = old_tab.send_text

    async def take_over_while_sending(text: str) -> None:
        # 옛 연결로 보내는 사이 새 연결이 붙는다. 새 연결은 아직 ready 를 받지 않았다
        stream._client._ws = new_tab
        await sending(text)

    old_tab.send_text = take_over_while_sending  # type: ignore[method-assign]
    await stream._on_transcript(transcript(0, 200, "기다린 말", is_final=True))

    assert new_tab.sent == []
    await stream.resend_missed()
    assert _texts(new_tab) == [("0-1", "기다린 말")]


@pytest.mark.anyio
async def test_successful_send_to_a_replaced_tab_also_reaches_the_caught_up_tab():
    stream = _final_stream(FakeTranscriptStore())
    old_tab = StuckWebSocket()
    await stream.attach(old_tab)
    showing = asyncio.create_task(
        stream._on_transcript(transcript(0, 200, "이어받은 말", is_final=True))
    )
    await old_tab.sending.wait()
    old_tab.stuck = False
    new_tab = FakeWebSocket()
    await stream.attach(new_tab)
    await stream.resend_missed()
    old_tab.release.set()
    await showing

    assert _texts(new_tab) == [("0-1", "이어받은 말")]


@pytest.mark.anyio
async def test_replay_does_not_switch_to_a_new_tab_before_its_ready():
    stream = _final_stream(FakeTranscriptStore())
    stream._transcripts.hold_missed(transcript_message("1-1", "첫"))
    stream._transcripts.hold_missed(transcript_message("1-2", "둘"))
    old_tab = StuckWebSocket()
    await stream.attach(old_tab)
    replay = asyncio.create_task(stream.resend_missed())
    await old_tab.sending.wait()
    old_tab.stuck = False
    new_tab = FakeWebSocket()
    await stream.attach(new_tab)
    old_tab.release.set()
    await replay

    assert new_tab.sent == []
    await stream.resend_missed()
    assert _texts(new_tab) == [("1-1", "첫"), ("1-2", "둘")]


@pytest.mark.anyio
async def test_holding_a_final_during_replay_does_not_pop_the_next_unsent_final(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(transcripts_module, "MAX_MISSED_FINALS", 2)
    stream = _final_stream(FakeTranscriptStore())
    first, second, third = [transcript_message(f"1-{i}", str(i)) for i in range(1, 4)]
    stream._transcripts.hold_missed(first)
    stream._transcripts.hold_missed(second)
    tab = StuckWebSocket()
    await stream.attach(tab)
    replay = asyncio.create_task(stream.resend_missed())
    await tab.sending.wait()
    stream._transcripts.hold_missed(third)
    tab.stuck = False
    tab.release.set()
    await replay

    assert _texts(tab) == [("1-1", "1"), ("1-2", "2"), ("1-3", "3")]


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
    assert record_service.transcript_cursor(db_session, TAKE_ID) == (2, 1)


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


PRIVATE_TRANSCRIPT = "PRIVATE_TRANSCRIPT_AUDIT_MARKER"


class _DriverError(Exception):
    """psycopg 예외처럼 SQLSTATE 를 들고 있다. 본문에는 DB 가 돌려준 값이 섞일 수 있다."""

    sqlstate = "08006"


@pytest.mark.anyio
async def test_save_failure_log_omits_transcript_text(caplog: pytest.LogCaptureFixture):
    """SQLAlchemy 예외 본문에는 SQL 매개변수가 붙는다. traceback 을 그대로 남기면 전사 원문이
    로그에 나간다. 예외 종류와 SQLSTATE 만 남기고 버퍼는 다음 저장 때 다시 넣는다."""
    error = OperationalError(
        "INSERT INTO take_transcript_segments ...",
        {"transcript": PRIVATE_TRANSCRIPT},
        _DriverError(f"connection lost while writing {PRIVATE_TRANSCRIPT}"),
    )
    store = FakeTranscriptStore(errors=[error])
    buffer = TranscriptBuffer(uuid.uuid7(), store)

    with caplog.at_level("ERROR"):
        assert await buffer.persist(transcript_message("1-1", PRIVATE_TRANSCRIPT), 100, 1)

    assert PRIVATE_TRANSCRIPT not in caplog.text
    assert "final 저장 실패" in caplog.text
    assert "error=OperationalError sqlstate=08006" in caplog.text
    assert f"len={len(PRIVATE_TRANSCRIPT)}" in caplog.text
    assert [seg.transcript for seg in buffer.unsaved] == [PRIVATE_TRANSCRIPT]

    assert await buffer.save_unsaved()
    assert [seg.transcript for seg in store.segments] == [PRIVATE_TRANSCRIPT]
    assert buffer.unsaved == []


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
    realtime = session.RealtimeSession(
        ws, take_id=TAKE_ID, db=db_session, stt_adapter=adapter, transcript_store=store
    )

    await realtime.run()

    assert [json.loads(t)["type"] for t in ws.sent] == ["ready"]
    assert [seg.transcript for seg in store.segments] == ["마지막 말"]
    assert adapter.sessions[0].controls == ["CloseStream"]
    assert registry.active_count() == 0


# ── Take 삭제 ─────────────────────────────────────────────────────────


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
    assert registry.active_count() == 0


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
    assert registry.active_count() == 0


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
    assert registry.active_count() == 0


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
        record_service.append_transcript_segment(db_session, uuid.uuid7(), segment)
    db_session.rollback()

    record_service.append_transcript_segment(db_session, TAKE_ID, segment)
    with pytest.raises(DuplicateTranscriptSegment):
        record_service.append_transcript_segment(db_session, TAKE_ID, segment)
