"""사용자별 동시 스트림(= Deepgram 연결) 상한."""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.stream import abandon, registry, runtime, state
from pitch_coach_backend.realtime.stream.registry import (
    MAX_STREAMS_PER_USER,
    StreamClosing,
    TooManyStreams,
)
from pitch_coach_backend.realtime.stt.adapter import SttConfig
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    _wait_until_ok,
    auth,
    closed_with,
    frame,
    make_take,
)


def _attach(adapter: FakeSttAdapter, owner_id: uuid.UUID, take_id: uuid.UUID | None = None):
    return registry.attach(
        take_id or uuid.uuid7(),
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )


async def _close_all() -> None:
    """테스트가 연 스트림을 닫는다. 남기면 다음 테스트 전에 루프가 닫혀 태스크가 버려진다."""
    streams = list(state.streams.values())
    for stream in streams:
        stream.cancel()
    for stream in streams:
        await stream.wait_closed(timeout=1.0)


def _open(owner_id: uuid.UUID) -> list:
    return [
        stream
        for stream in state.streams.values()
        if stream.owner_id == owner_id and not stream.is_stopping
    ]


@pytest.mark.anyio
async def test_a_second_take_is_rejected_while_the_first_take_is_attached():
    """사용자는 한 번에 연습 하나만 한다. 첫 Take는 유지하고 다른 Take의 연결을 거절한다.
    거절한 Take는 아무도 다시 붙지 않으면 떠난 것으로 끝낸다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    first = await _attach(adapter, owner_id)
    rejected = uuid.uuid7()

    with pytest.raises(TooManyStreams):
        await _attach(adapter, owner_id, rejected)

    assert _open(owner_id) == [first]
    assert first.has_client and not first.is_stopping
    assert rejected not in state.streams
    assert abandon.armed(rejected)
    await asyncio.sleep(0.05)
    assert len(adapter.sessions) == 1
    await _close_all()


@pytest.mark.anyio
async def test_stream_waiting_for_a_reconnect_makes_room_for_a_new_take():
    """연결이 끊겨 grace 를 기다리는 스트림은 오래된 것부터 정리해 자리를 만든다. 그 Take 는 끝나지
    않는다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    previous = await _attach(adapter, owner_id)
    tab = previous._client.ws
    previous.push(frame(1, 0), source=tab)
    previous.detach(tab)

    fresh = await _attach(adapter, owner_id)

    assert previous.is_finished
    assert _open(owner_id) == [fresh]
    assert previous.take_id not in state.ended
    await _close_all()


@pytest.mark.anyio
async def test_reconnect_to_the_same_take_and_other_users_are_not_counted():
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    streams = [await _attach(adapter, owner_id) for _ in range(MAX_STREAMS_PER_USER)]

    # 새로고침 · 탭 이어받기는 같은 스트림에 붙는다
    again = await _attach(adapter, owner_id, streams[0].take_id)
    other = await _attach(adapter, uuid.uuid7())

    assert again is streams[0]
    assert other in state.streams.values()
    assert len(_open(owner_id)) == MAX_STREAMS_PER_USER
    await _close_all()


@pytest.mark.anyio
async def test_simultaneous_connections_of_one_user_do_not_pass_the_limit():
    """Take 마다 잠금이 따로라 같은 사용자의 연결이 동시에 와도 자리를 각자 보지 않아야 한다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()

    results = await asyncio.gather(
        *(_attach(adapter, owner_id) for _ in range(3)),
        return_exceptions=True,
    )

    rejected = [r for r in results if isinstance(r, TooManyStreams)]
    assert len(rejected) == 2
    assert len(_open(owner_id)) == 1
    await _close_all()


@pytest.mark.anyio
async def test_draining_streams_still_occupy_a_deepgram_slot():
    # CloseStream 뒤 마지막 전사를 기다리는 중에도 실제 Deepgram 세션은 열려 있다.
    adapter = FakeSttAdapter(*(Plan(swallow_close=True) for _ in range(8)))
    owner_id = uuid.uuid7()
    streams = [await _attach(adapter, owner_id) for _ in range(MAX_STREAMS_PER_USER)]
    for stream in streams:
        await _wait_until_ok(stream)

    old = streams[0]
    old.push(frame(1, 0), source=old._client.ws)
    old.detach(old._client.ws)
    attaching = asyncio.create_task(_attach(adapter, owner_id))
    competing = asyncio.create_task(_attach(adapter, owner_id))
    try:
        # 충분히 이벤트 루프를 돌려도 옛 세션이 살아 있는 동안 새 세션을 열면 안 된다.
        for _ in range(20):
            await asyncio.sleep(0)
        live = [session for session in adapter.sessions if not session.aborted]
        assert len(live) <= MAX_STREAMS_PER_USER
        assert not attaching.done()
        assert not competing.done()
        # 한 사용자의 drain 대기는 다른 사용자의 연결을 붙잡지 않는다.
        other = await asyncio.wait_for(_attach(FakeSttAdapter(), uuid.uuid7()), timeout=0.5)
        assert other.has_client
        old.cancel()
        await old.wait_closed(timeout=1.0)
        fresh = await asyncio.wait_for(attaching, timeout=1.0)
        with pytest.raises(TooManyStreams):
            await competing
        await _wait_until_ok(fresh)
        assert len([s for s in adapter.sessions if not s.aborted]) <= MAX_STREAMS_PER_USER
        assert not state.owner_locks
    finally:
        attaching.cancel()
        competing.cancel()
        await asyncio.gather(attaching, competing, return_exceptions=True)
        await _close_all()


def test_rejected_connection_gets_the_error_and_a_policy_close(
    client: TestClient, stt: FakeSttAdapter, token: str, monkeypatch: pytest.MonkeyPatch
):
    """FE 는 1008 이면 다시 붙지 않는다. 처음 연결이면 발표를 시작하지 않는다."""
    monkeypatch.setattr(registry, "MAX_STREAMS_PER_USER", 0)

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        error = ws.receive_json()
        code = closed_with(ws)

    assert (error["type"], error["code"]) == ("error", "TOO_MANY_STREAMS")
    assert code == 1008
    assert TAKE_ID not in state.streams
    assert stt.sessions == []


@pytest.mark.anyio
async def test_has_room_counts_only_attached_streams_of_other_takes():
    """첫 연결이 Take 를 시작하기 전에 묻는 자리 (`has_room`). `_make_room` 이 비울 스트림은 세지
    않는다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    first = await _attach(adapter, owner_id)

    assert not registry.has_room(owner_id, uuid.uuid7())
    # 같은 Take 로 다시 붙는 연결은 자리가 필요 없다 · 다른 사용자는 세지 않는다
    assert registry.has_room(owner_id, first.take_id)
    assert registry.has_room(uuid.uuid7(), uuid.uuid7())

    tab = first._client.ws
    first.push(frame(1, 0), source=tab)
    first.detach(tab)
    # 다시 붙기를 기다리는 스트림은 새 Take 가 정리하고 자리를 쓴다
    assert registry.has_room(owner_id, uuid.uuid7())
    await _close_all()


def test_ready_take_is_not_started_when_there_is_no_room(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    """자리가 없으면 첫 연결이 Take 를 시작하지 않는다. 떠난 Take 로 끝나 기록에 남지 않는다."""
    monkeypatch.setattr(registry, "MAX_STREAMS_PER_USER", 0)
    ready_take = make_take(db_session, user.id, status="READY")

    with client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        auth(ws, create_access_token(user.id))
        error = ws.receive_json()
        code = closed_with(ws)

    assert (error["type"], error["code"]) == ("error", "TOO_MANY_STREAMS")
    assert code == 1008
    db_session.refresh(ready_take)
    assert ready_take.status == "READY"
    assert not abandon.armed(ready_take.id)
    assert stt.sessions == []


@pytest.mark.anyio
async def test_attached_stream_without_audio_makes_room_for_a_new_take(
    monkeypatch: pytest.MonkeyPatch,
):
    """브라우저가 닫힘 프레임 없이 사라진 연결은 서버에 붙어 있는 것으로 남는다. 오디오가
    `LIVE_AUDIO_SEC` 넘게 없으면 새 Take 에 자리를 내준다. 그 연결은 다시 붙지 않게 닫고 Take 는
    끝내지 않는다."""
    monkeypatch.setattr(runtime, "LIVE_AUDIO_SEC", 0.05)
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    previous = await _attach(adapter, owner_id)
    ghost = previous._client.ws
    previous.push(frame(1, 0), source=ghost)
    await asyncio.sleep(0.06)

    assert registry.has_room(owner_id, uuid.uuid7())
    fresh = await _attach(adapter, owner_id)

    assert previous.is_finished
    assert _open(owner_id) == [fresh]
    assert previous.take_id not in state.ended
    assert abandon.armed(previous.take_id)
    await asyncio.sleep(0.01)
    assert json.loads(ghost.sent[-1])["code"] == "TOO_MANY_STREAMS"
    assert ghost.closed is not None and ghost.closed[0] == 1008
    # 닫힌 연결이 뒤늦게 떨어져도 새 스트림에는 영향이 없다
    previous.detach(ghost)
    assert fresh.has_client
    await _close_all()


@pytest.mark.anyio
async def test_stream_with_recent_audio_keeps_its_place(monkeypatch: pytest.MonkeyPatch):
    """막 붙었거나 오디오가 오는 연결은 다른 곳에서 발표 중일 수 있어 정리하지 않는다."""
    monkeypatch.setattr(runtime, "LIVE_AUDIO_SEC", 0.5)
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    first = await _attach(adapter, owner_id)
    await asyncio.sleep(0.3)
    first.push(frame(1, 0), source=first._client.ws)
    await asyncio.sleep(0.3)

    # 붙은 지는 0.5 초가 넘었지만 마지막 오디오는 그보다 최근이다
    assert not first.is_idle
    with pytest.raises(TooManyStreams):
        await _attach(adapter, owner_id)
    assert first.has_client and not first.is_stopping
    await _close_all()


@pytest.mark.anyio
async def test_previous_stream_that_does_not_close_asks_to_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    """정리할 스트림이 제한 시간 안에 닫히지 않으면 다른 Take 가 발표 중인 것과 구분해 알린다.
    옛 스트림은 레지스트리에 남아 계속 세고, 첫 연결이 시작한 Take 에는 떠남 타이머를 건다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    previous = await _attach(adapter, owner_id)
    tab = previous._client.ws
    previous.push(frame(1, 0), source=tab)
    previous.detach(tab)

    async def never_closes(timeout: float | None = None) -> bool:
        return False

    monkeypatch.setattr(previous, "wait_closed", never_closes)
    waiting = uuid.uuid7()

    with pytest.raises(StreamClosing):
        await _attach(adapter, owner_id, waiting)

    assert state.streams.get(previous.take_id) is previous
    assert waiting not in state.streams
    assert abandon.armed(waiting)
    monkeypatch.undo()
    await _close_all()


def test_retry_close_has_no_error_and_lets_the_client_reconnect(
    client: TestClient, stt: FakeSttAdapter, token: str, monkeypatch: pytest.MonkeyPatch
):
    """FE 는 1013 이면 백오프로 다시 붙는다. 오류 안내를 띄우지 않게 오류 메시지는 보내지 않는다."""

    async def closing(*args, **kwargs):
        raise StreamClosing

    monkeypatch.setattr(registry, "attach", closing)

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        code = closed_with(ws)

    assert code == 1013
    assert stt.sessions == []


@pytest.mark.anyio
async def test_ending_stream_is_not_cancelled_to_make_room(monkeypatch: pytest.MonkeyPatch):
    """끝내는 중인(stop) 스트림은 끝내는 쪽이 마지막 전사를 기다린다. 새 Take 자리를 만들려고
    먼저 끊으면 끝낸 Take 의 마지막 문장이 빠진다. 끝내는 쪽의 상한까지 기다리고, 그래도 안 닫히면
    다시 붙게 알린다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    ending = await _attach(adapter, owner_id)
    ending.push(frame(1, 0), source=ending._client.ws)
    ending.end()
    waits: list[float | None] = []
    cancels: list[bool] = []

    async def slow(timeout: float | None = None) -> bool:
        waits.append(timeout)
        return False

    monkeypatch.setattr(ending, "wait_closed", slow)
    monkeypatch.setattr(ending, "cancel", lambda: cancels.append(True))

    with pytest.raises(StreamClosing):
        await _attach(adapter, owner_id)

    assert waits == [registry.ENDING_WAIT_SEC]
    assert cancels == []
    monkeypatch.undo()
    await _close_all()


@pytest.mark.anyio
async def test_new_take_opens_after_the_ending_stream_closes_by_itself():
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    ending = await _attach(adapter, owner_id)
    await _wait_until_ok(ending)
    ending.push(frame(1, 0), source=ending._client.ws)
    ending.end()

    fresh = await asyncio.wait_for(_attach(adapter, owner_id), timeout=2.0)

    assert ending.is_finished
    assert _open(owner_id) == [fresh]
    await _close_all()
