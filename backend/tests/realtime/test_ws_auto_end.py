"""자동 종료 — 말 없음 · 최대 시간."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.dto import (
    AutoEndReason,
)
from pitch_coach_backend.realtime.storage.store import DbTranscriptStore
from pitch_coach_backend.realtime.stream import registry, state
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttConfig,
    Transcript,
)
from tests.realtime.conftest import (
    SHORT_SILENCE,
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    _wait_for,
    _wait_until_ok,
    auth,
    closed_with,
    empty_final,
    frame,
    handshake,
    make_stream,
    make_take,
    receive_auto_end,
    short_time_limit,
    stop,
    transcript,
    use,
    use_policy,
    use_store,
    wait_state,
)

# ── 자동 종료 ─────────────────────────────────────────────────────────


def auto_end_reason(stream: TakeStream) -> AutoEndReason | None:
    """이 프로세스가 그 Take 를 자동으로 끝냈으면 그 이유."""
    return state.ended.get(stream.take_id)


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
    assert registry.active_count() == 0


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
    assert rejected == {
        "type": "error",
        "code": "TAKE_ENDED",
        "message": message,
        "end_reason": "TIME_LIMIT",
    }
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
    assert rejected == {
        "type": "error",
        "code": "TAKE_ENDED",
        "message": message,
        "end_reason": "SILENCE",
    }
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
    assert registry.active_count() == 0


def test_end_reason_write_failure_still_closes_the_take(
    client: TestClient,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """DB 가 죽어 이유를 못 적어도 Take 는 끝낸다. 비용을 막는 게 먼저다. 적기는 뒤에서 다시
    한다."""
    use_policy(monkeypatch, short_time_limit(1_000))
    use_store(FakeTranscriptStore(fail_mark=True))
    use(FakeSttAdapter())
    with caplog.at_level("WARNING"), client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 950))
        receive_auto_end(ws)
        wait_state(ws, "closed")
        assert closed_with(ws) == 1000

    assert "뒤에서 마저 적는다" in caplog.text
    assert state.ended == {TAKE_ID: AutoEndReason.TIME_LIMIT}


def test_end_reason_is_written_to_the_database_once(db_session: Session, take: Take):
    """Take 를 끝난 상태로 바꾸고 이유를 적는다. 먼저 적힌 이유가 남고, 그 사이 지워진 Take 는
    조용히 넘어간다."""
    assert take.ended_at is None
    assert lifecycle_service.mark_ended(db_session, TAKE_ID, "SILENCE") is True
    assert lifecycle_service.mark_ended(db_session, TAKE_ID, "TIME_LIMIT") is False
    db_session.refresh(take)
    assert (take.status, take.end_reason) == ("ANALYZING", "SILENCE")
    assert take.ended_at is not None, "FE 가 떨어져 있어도 끝난 시각이 남는다"
    assert lifecycle_service.mark_ended(db_session, uuid.uuid7(), "SILENCE") is False


def test_end_reason_is_not_written_to_a_take_that_already_ended(db_session: Session, user: User):
    """이미 끝난 Take(사용자가 끝내 분석 중 등)는 자동 종료로 바꾸지 않는다. FE 가 적은 끝난 시각도
    덮지 않는다."""
    ended = make_take(db_session, user.id, status="COMPLETED")
    assert lifecycle_service.mark_ended(db_session, ended.id, "TIME_LIMIT") is False
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

    with pytest.raises(registry.TakeEnded) as info:
        await registry.attach(
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
        return registry.create_stream(
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
        async def mark_ended(
            self, take_id: uuid.UUID, end_reason: str | None = None, utterance_ends=None
        ) -> bool:
            await asyncio.sleep(0.2)
            return await super().mark_ended(take_id, end_reason, utterance_ends)

    store = SlowStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(1_000))
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 950))

    await registry.stop_all()

    assert store.auto_ended == [(stream.take_id, "TIME_LIMIT")]


@pytest.mark.anyio
async def test_server_clock_does_not_count_time_without_a_stream():
    """탭이 죽어 스트림이 없던 시간은 세지 않는다. 벽시계로 재면 한참 뒤 이어서 연습하자마자
    끝나 버린다 (무대 시계는 끊긴 동안 멈춰 있다)."""
    take_id = uuid.uuid7()
    policy = short_time_limit(400)

    def make() -> TakeStream:
        return registry.create_stream(
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
        stream = registry.create_stream(
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
        await _wait_for(lambda: "Take 종료를 적지 않았다" in caplog.text)


@pytest.mark.anyio
async def test_shutdown_cancels_cleanup_that_does_not_finish_and_says_which(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """정리가 상한 안에 안 끝나면 루프가 닫히며 조용히 사라지게 두지 않고, 무엇을 끊는지 남긴다."""

    class StuckStore(FakeTranscriptStore):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()

        async def mark_ended(
            self, take_id: uuid.UUID, end_reason: str | None = None, utterance_ends=None
        ) -> bool:
            self.entered.set()
            await asyncio.Event().wait()
            return True

    monkeypatch.setattr(registry, "MARK_AUTO_END_TIMEOUT_SEC", 10.0)
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 0.1)
    store = StuckStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(1_000))
    stream.start()
    await _wait_until_ok(stream)
    stream.push(frame(1, 950))
    # 스트림을 정리하고 DB 쓰기(상한 10초)에 들어갈 때까지 기다린다
    await asyncio.wait_for(store.entered.wait(), 2.0)

    # 상한(10.1초)을 기다리지 않게 줄인다. 스트림 정리가 끝난 뒤의 대기만 본다
    monkeypatch.setattr(registry, "MARK_AUTO_END_TIMEOUT_SEC", 0.1)
    with caplog.at_level("ERROR"):
        await registry.stop_all()

    assert f"take-auto-end:{stream.take_id}" in caplog.text
    assert not state.background
