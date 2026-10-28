"""사용자별 동시 스트림(= Deepgram 연결) 상한."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from pitch_coach_backend.realtime.stream import abandon, registry, state
from pitch_coach_backend.realtime.stream.registry import MAX_STREAMS_PER_USER, TooManyStreams
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
async def test_connection_over_the_limit_is_rejected_while_every_stream_is_attached():
    """다른 창에서 발표 중일 수 있는 스트림은 정리하지 않는다. 새 연결을 거절하고 Deepgram 연결은
    상한을 넘지 않는다. 거절한 Take 는 아무도 다시 붙지 않으면 떠난 것으로 끝낸다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    for _ in range(MAX_STREAMS_PER_USER):
        await _attach(adapter, owner_id)
    rejected = uuid.uuid7()

    with pytest.raises(TooManyStreams):
        await _attach(adapter, owner_id, rejected)

    assert len(_open(owner_id)) == MAX_STREAMS_PER_USER
    assert rejected not in state.streams
    assert abandon.armed(rejected)
    await asyncio.sleep(0.05)
    assert len(adapter.sessions) == MAX_STREAMS_PER_USER
    await _close_all()


@pytest.mark.anyio
async def test_stream_waiting_for_a_reconnect_makes_room_for_a_new_take():
    """연결이 끊겨 grace 를 기다리는 스트림은 오래된 것부터 정리해 자리를 만든다. 그 Take 는 끝나지
    않는다."""
    adapter = FakeSttAdapter()
    owner_id = uuid.uuid7()
    streams = [await _attach(adapter, owner_id) for _ in range(MAX_STREAMS_PER_USER)]
    oldest, newer = streams[0], streams[1]
    for stream in (oldest, newer):
        tab = stream._client.ws
        stream.push(frame(1, 0), source=tab)
        stream.detach(tab)

    fresh = await _attach(adapter, owner_id)

    assert oldest.is_stopping
    assert not newer.is_stopping
    assert fresh in _open(owner_id)
    assert len(_open(owner_id)) == MAX_STREAMS_PER_USER
    assert oldest.take_id not in state.ended
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
        *(_attach(adapter, owner_id) for _ in range(MAX_STREAMS_PER_USER + 2)),
        return_exceptions=True,
    )

    rejected = [r for r in results if isinstance(r, TooManyStreams)]
    assert len(rejected) == 2
    assert len(_open(owner_id)) == MAX_STREAMS_PER_USER
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
