"""떠난 Take — 끊긴 방식별 대기 타이머 · 창을 다시 연 FE · 앱 재시작 복구."""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.storage.store import DbTranscriptStore
from pitch_coach_backend.realtime.stream import runtime as take_stream
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt.adapter import (
    SttConfig,
)
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    _wait_for,
    _wait_until_ok,
    auth,
    closed_with,
    frame,
    handshake,
    make_take,
    use,
    wait_state,
)

# ── WS-1 재연결 (FE 가 끊긴다) ─────────────────────────────────────────


def test_closed_tab_is_failed_after_a_short_wait(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch, store: FakeTranscriptStore
):
    """창을 닫으면(닫힘 코드 1001) 긴 대기 없이 짧게 기다렸다 실패(CLOSED)로 끝낸다. 그 사이 다시
    붙지 않았으니 새로고침이 아니다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    monkeypatch.setattr(take_stream, "CLOSED_TAB_WAIT_SEC", 0.1)
    use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.close(code=1001)

    deadline = time.monotonic() + 3.0
    while not store.abandoned and time.monotonic() < deadline:
        time.sleep(0.02)

    assert [take_id for take_id, _ in store.abandoned] == [TAKE_ID]
    assert store.abandon_reasons == ["CLOSED"]


def test_connection_lost_without_closing_the_tab_waits_long(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch, store: FakeTranscriptStore
):
    """1001 이 아니면(네트워크 · FE 가 재연결을 포기함) FE 가 발표를 이어 가는 중일 수 있다.
    창을 닫았을 때의 짧은 대기를 쓰지 않는다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    monkeypatch.setattr(take_stream, "CLOSED_TAB_WAIT_SEC", 0.05)
    use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.close(code=1006)

    deadline = time.monotonic() + 3.0
    while TAKE_ID not in take_stream._abandon_timers and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)

    assert TAKE_ID in take_stream._abandon_timers
    assert store.abandoned == []


# ── 떠난 Take ─────────────────────────────────────────────────────────


def make_plain_stream(take_id: uuid.UUID, store: FakeTranscriptStore) -> TakeStream:
    return TakeStream(
        take_id,
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )


def abandon_wait_left(take_id: uuid.UUID) -> float:
    """떠난 것으로 끝낼 때까지 남은 초."""
    return take_stream._abandon_timers[take_id].when() - asyncio.get_running_loop().time()


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

    # 그 뒤로 다시 붙으면 거절한다. 떠난 것으로 끝낸 이유를 싣는다
    with pytest.raises(take_stream.TakeEnded) as info:
        await take_stream.attach(
            stream.take_id,
            FakeWebSocket(),
            owner_id=stream.owner_id,
            stt_adapter=FakeSttAdapter(),
            config=SttConfig(),
            store=store,
        )
    assert info.value.reason == "ABANDONED"


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
async def test_abandon_waits_the_same_however_much_of_the_talk_is_left(
    monkeypatch: pytest.MonkeyPatch,
):
    """창을 닫지 않고 끊겼으면 발표가 얼마나 남았든 ABANDON_AFTER_SEC 만 기다린다. 그 뒤로 STT 없이
    발표를 이어 간 FE 는 끝내기 PUT 이 되살린다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    stream = make_plain_stream(uuid.uuid7(), FakeTranscriptStore())

    await open_and_leave(stream)

    wait_sec = take_stream.ABANDON_AFTER_SEC
    assert wait_sec - 1 < abandon_wait_left(stream.take_id) <= wait_sec


@pytest.mark.anyio
async def test_refresh_after_closing_the_tab_keeps_the_take(monkeypatch: pytest.MonkeyPatch):
    """새로고침도 1001 로 끊긴다. 짧은 대기 안에 다시 붙으면 이어서 연습한다."""
    monkeypatch.setattr(take_stream, "GRACE_SEC", 0.05)
    monkeypatch.setattr(take_stream, "CLOSED_TAB_WAIT_SEC", 0.2)
    store = FakeTranscriptStore()
    stream = make_plain_stream(uuid.uuid7(), store)
    tab = FakeWebSocket()
    take_stream._streams[stream.take_id] = stream
    stream.start()
    await stream.attach(tab)
    await _wait_until_ok(stream)
    stream.push(frame(1, 0), source=tab)
    stream.detach(tab, tab_closed=True)
    assert await stream.wait_closed(timeout=2.0)
    assert 0 < abandon_wait_left(stream.take_id) <= 0.2

    await take_stream.attach(
        stream.take_id,
        FakeWebSocket(),
        owner_id=stream.owner_id,
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )
    await asyncio.sleep(0.3)

    assert store.abandoned == []
    assert stream.take_id not in take_stream._ended


@pytest.mark.anyio
async def test_reopened_tab_cuts_the_stream_and_blocks_reconnect():
    """창을 다시 연 FE 가 Take 를 실패로 끝냈다. 브라우저는 닫혔는데 서버가 아직 붙어 있다고 믿는
    연결이 있으면 TAKE_ENDED 로 끊고, 떠난 Take 타이머 없이 스트림을 닫는다. 다시 붙지 못한다."""
    store = FakeTranscriptStore()
    stream = make_plain_stream(uuid.uuid7(), store)
    tab = FakeWebSocket()
    take_stream._streams[stream.take_id] = stream
    stream.start()
    await stream.attach(tab)
    await _wait_until_ok(stream)

    await take_stream.end_closed_tab(stream.take_id)

    assert await stream.wait_closed(timeout=2.0)
    await _wait_for(lambda: tab.closed is not None)
    assert json.loads(tab.sent[-1]) == {
        "type": "error",
        "code": "TAKE_ENDED",
        "message": "이미 끝난 연습입니다.",
        "end_reason": "CLOSED",
    }
    assert tab.closed == (1008, "TAKE_ENDED")
    assert take_stream._abandon_timers == {}
    with pytest.raises(take_stream.TakeEnded) as info:
        await take_stream.attach(
            stream.take_id,
            FakeWebSocket(),
            owner_id=stream.owner_id,
            stt_adapter=FakeSttAdapter(),
            config=SttConfig(),
            store=store,
        )
    assert info.value.reason == "CLOSED", "다시 붙으면 창을 닫아 끝났다고 알린다"


@pytest.mark.anyio
async def test_take_is_live_only_while_audio_keeps_coming(monkeypatch: pytest.MonkeyPatch):
    """연결이 붙어 있고 최근 오디오가 왔을 때만 발표 중이다. 브라우저가 닫힘 프레임 없이 사라져
    서버만 연결이 붙어 있다고 믿으면 오디오가 멈춰 발표 중이 아니다."""
    monkeypatch.setattr(take_stream, "LIVE_AUDIO_SEC", 0.1)
    stream = make_plain_stream(uuid.uuid7(), FakeTranscriptStore())
    tab = FakeWebSocket()
    take_stream._streams[stream.take_id] = stream
    stream.start()
    await stream.attach(tab)
    await _wait_until_ok(stream)
    assert not take_stream.is_live(stream.take_id), "붙기만 하고 오디오가 없다"

    stream.push(frame(1, 0), source=tab)
    assert take_stream.is_live(stream.take_id)

    await asyncio.sleep(0.15)
    assert not take_stream.is_live(stream.take_id), "오디오가 멈췄다"
    assert not take_stream.is_live(uuid.uuid7()), "스트림이 없다"


@pytest.mark.anyio
async def test_reopened_tab_turns_off_the_wait_timer():
    """스트림이 이미 닫혀 타이머만 남았으면 그 타이머를 끈다. DB 에는 API 가 먼저 적었다."""
    store = FakeTranscriptStore()
    take_id = uuid.uuid7()
    take_stream.arm_abandon(take_id, store, closed_at=datetime.now(UTC))

    await take_stream.end_closed_tab(take_id)

    assert take_stream._abandon_timers == {}
    assert take_id in take_stream._ended


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
async def test_startup_rearms_timers_from_the_last_activity():
    """앱이 다시 뜨면 메모리의 타이머가 없다. 진행 중으로 남은 Take 를 DB 에서 읽어 마지막 활동에서
    잰 남은 시간으로 다시 건다. 이미 지난 Take 는 바로 끝낸다."""
    wait_sec = take_stream.ABANDON_AFTER_SEC
    store = FakeTranscriptStore()
    now = datetime.now(UTC)
    stale, recent = uuid.uuid7(), uuid.uuid7()
    stale_at = now - timedelta(seconds=wait_sec + 1)
    store.running = [(stale, stale_at), (recent, now - timedelta(seconds=20))]

    await take_stream.arm_abandoned_takes(store)

    await _wait_for(lambda: store.abandoned)
    assert store.abandoned == [(stale, stale_at)]
    assert wait_sec - 21 < abandon_wait_left(recent) <= wait_sec - 20


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
    assert lifecycle_service.mark_abandoned(db_session, TAKE_ID, closed_at) is True
    assert lifecycle_service.mark_abandoned(db_session, TAKE_ID, datetime.now(UTC)) is False
    db_session.refresh(take)
    assert (take.status, take.end_reason, take.ended_at) == ("FAILED", "ABANDONED", closed_at)
    assert lifecycle_service.mark_abandoned(db_session, uuid.uuid7(), closed_at) is False


def test_abandon_does_not_touch_a_take_that_already_ended(db_session: Session, user: User):
    ended = make_take(db_session, user.id, status="ANALYZING")
    assert lifecycle_service.mark_abandoned(db_session, ended.id, datetime.now(UTC)) is False
    db_session.refresh(ended)
    assert (ended.status, ended.end_reason) == ("ANALYZING", None)


def test_end_revives_a_take_ended_as_abandoned(db_session: Session, user: User):
    """재연결을 포기하고 STT 없이 발표를 이어 간 FE 의 끝내기 PUT 이다. 떠난 것으로 끝낸 Take 만
    되살린다. 끝낸 시각은 PUT 이 먼저 적는다 (여기서는 비어 있을 때만 채운다)."""
    abandoned = make_take(db_session, user.id)
    failed = make_take(db_session, user.id, status="FAILED")
    closed_at = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert lifecycle_service.mark_abandoned(db_session, abandoned.id, closed_at) is True

    assert lifecycle_service.mark_ended(db_session, abandoned.id) is True
    assert lifecycle_service.mark_ended(db_session, failed.id) is False

    db_session.refresh(abandoned)
    db_session.refresh(failed)
    assert (abandoned.status, abandoned.end_reason, abandoned.ended_at) == (
        "ANALYZING",
        None,
        closed_at,
    )
    assert failed.status == "FAILED"


def test_end_does_not_revive_a_take_closed_with_its_tab(db_session: Session, user: User):
    """창을 닫아 끝낸 Take(CLOSED)는 이어 가지 않는다. 끝내기 PUT 이 와도 되살리지 않는다."""
    closed = make_take(db_session, user.id)
    assert (
        lifecycle_service.mark_abandoned(db_session, closed.id, datetime.now(UTC), "CLOSED")
        is True
    )

    assert lifecycle_service.mark_ended(db_session, closed.id) is False

    db_session.refresh(closed)
    assert (closed.status, closed.end_reason) == ("FAILED", "CLOSED")


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

    running = dict(lifecycle_service.running_takes(db_session))

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


@pytest.mark.parametrize("end_reason", ["ABANDONED", "CLOSED"])
def test_abandoned_take_in_the_database_is_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User, end_reason: str
):
    """프로세스가 다시 떠도 DB 가 막는다. 자동 종료가 아니라 auto_end 없이 일반 문구로. FE 가
    창을 닫은 Take(CLOSED)와 STT 없이 발표를 이어 가도 되는 Take(ABANDONED)를 가르도록 이유를
    싣는다."""
    left = make_take(db_session, user.id, status="FAILED")
    left.end_reason = end_reason
    db_session.commit()

    with client.websocket_connect(f"/api/ws/takes/{left.id}") as ws:
        auth(ws, create_access_token(user.id))
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    assert rejected == {
        "type": "error",
        "code": "TAKE_ENDED",
        "message": "이미 끝난 연습입니다.",
        "end_reason": end_reason,
    }
    assert stt.configs == []


def test_take_ended_by_the_user_is_rejected_without_a_reason(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """사용자가 끝낸 Take 는 이유가 없다. end_reason 을 싣지 않는다."""
    ended = make_take(db_session, user.id, status="ANALYZING")
    db_session.commit()

    with client.websocket_connect(f"/api/ws/takes/{ended.id}") as ws:
        auth(ws, create_access_token(user.id))
        rejected = ws.receive_json()

    assert rejected == {"type": "error", "code": "TAKE_ENDED", "message": "이미 끝난 연습입니다."}
