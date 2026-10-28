"""Take 상태 — READY → RUNNING → ANALYZING · stop · 끝내기 PUT."""

import json
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.storage.store import DbTranscriptStore
from pitch_coach_backend.realtime.stream import finish, registry, state
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import SttConfig
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    auth,
    closed_with,
    db_down,
    frame,
    handshake,
    make_take,
    stop,
    transcript,
    use,
    use_store,
    wait_state,
)

# ── stop ──────────────────────────────────────────────────────────────


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


# ── 소유권·정리 (코드 리뷰 지적) ───────────────────────────────────────


def test_reconnect_while_stopping_is_rejected(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """사용자가 끝낸 Take 다. 마지막 전사를 정리하는 동안(DB 에 적기 전) 붙는 연결도 받지 않는다 —
    받으면 새 스트림이 열려 끝난 연습 뒤에 전사가 더 붙는다. 종료 중 새로고침한 FE 가 stop 만
    보내러 붙어도 이렇게 거절되고, FE 는 stop 을 보낸 상태에서 닫히면 종료를 그대로 이어 간다."""
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 0.3)
    adapter = use(FakeSttAdapter(Plan(swallow_close=True)))

    with client.websocket_connect(WS_PATH) as first:
        handshake(first, token)
        wait_state(first, "ok")
        first.send_text(json.dumps({"type": "stop"}))  # 응답을 기다리지 않는다
        deadline = time.monotonic() + 3.0
        while TAKE_ID not in state.ended and time.monotonic() < deadline:
            time.sleep(0.01)

        with client.websocket_connect(WS_PATH) as second:
            auth(second, token)
            assert second.receive_json()["code"] == "TAKE_ENDED"
            assert closed_with(second) == 1008

        wait_state(first, "closed")

    assert len(adapter.sessions) == 1, "새 Deepgram 세션을 열지 않는다"


# ── Take 연결: 존재·소유·상태 검사 ──────────────────────────────────────


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


@pytest.mark.parametrize("transient", [False, True])
def test_running_write_failure_closes_before_ready_and_allows_reconnection(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    transient: bool,
):
    """시작 값이 고정되지 않았으면 발표를 열지 않는다. DB 복구 후 재연결로 다시 시작한다."""
    real = lifecycle_service.mark_running
    calls = []

    def fail(db: Session, take_id: uuid.UUID) -> bool:
        calls.append(take_id)
        if transient:
            raise db_down()
        raise RuntimeError("db down")

    monkeypatch.setattr(lifecycle_service, "mark_running", fail)
    ready_take = make_take(db_session, user.id, status="READY")
    with caplog.at_level("ERROR"), client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        auth(ws, create_access_token(user.id))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()

    assert closed.value.code == 1011
    assert len(calls) == (2 if transient else 1)
    assert stt.sessions == []
    assert ready_take.id not in state.takes
    db_session.refresh(ready_take)
    assert ready_take.status == "READY"
    assert "RUNNING 으로 못 바꿨다" in caplog.text

    monkeypatch.setattr(lifecycle_service, "mark_running", real)
    with client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        handshake(ws, create_access_token(user.id))
        wait_state(ws, "ok")
        stop(ws)
    db_session.refresh(ready_take)
    assert ready_take.status == "RUNNING"


def test_take_ended_while_starting_is_rejected_with_its_reason(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    """상태를 읽은 뒤 RUNNING 으로 바꾸기 전에 시작하지 않은 Take 로 끝났다(정리 작업). 다시 읽어
    끝난 Take 로 거절한다 — FAILED Take 에 STT 를 붙이지 않는다."""
    ready_take = make_take(db_session, user.id, status="READY")

    def swept_first(db: Session, take_id: uuid.UUID) -> bool:
        db.execute(
            update(Take).where(Take.id == take_id).values(status="FAILED", end_reason="ABANDONED")
        )
        return TakeRepository(db).mark_running(take_id)

    monkeypatch.setattr(lifecycle_service, "mark_running", swept_first)
    with client.websocket_connect(f"/api/ws/takes/{ready_take.id}") as ws:
        auth(ws, create_access_token(user.id))
        rejected = ws.receive_json()
        assert closed_with(ws) == 1008

    assert (rejected["code"], rejected["end_reason"]) == ("TAKE_ENDED", "ABANDONED")
    assert stt.configs == []


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
    """DB 가 죽어 종료를 못 적어도 FE 에는 평소처럼 closed 를 보내고 닫는다. 적기는 뒤에서 다시
    한다. 이 프로세스 안에서는 다시 붙는 연결을 계속 막는다."""
    use_store(FakeTranscriptStore(fail_mark=True))
    adapter = use(FakeSttAdapter())
    with caplog.at_level("WARNING"), client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        stop(ws)
        assert closed_with(ws) == 1000
    assert "뒤에서 마저 적는다" in caplog.text

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(adapter.sessions) == 1


def test_take_status_moves_ready_running_analyzing_in_the_database(db_session: Session, user: User):
    """연결이 READY 를 RUNNING 으로, 끝내기가 ANALYZING 으로 바꾼다. 두 번 불러도 한 번만 바뀐다."""
    take = make_take(db_session, user.id, status="READY")
    assert lifecycle_service.mark_running(db_session, take.id) is True
    assert lifecycle_service.mark_running(db_session, take.id) is False, (
        "다시 붙은 연결은 그대로 둔다"
    )
    db_session.refresh(take)
    assert take.status == "RUNNING"

    assert lifecycle_service.mark_ended(db_session, take.id) is True
    assert lifecycle_service.mark_ended(db_session, take.id) is False
    db_session.refresh(take)
    assert take.status == "ANALYZING"
    assert take.ended_at is not None, "FE 가 종료 시각을 못 적어도 남는다"
    assert lifecycle_service.mark_running(db_session, take.id) is False, (
        "끝난 Take 는 다시 열지 않는다"
    )
    assert lifecycle_service.mark_ended(db_session, uuid.uuid7()) is False

    # 시작하지 않은 Take 는 끝내지 않는다. 실시간 연결이 한 번도 붙지 않아 남은 기록이 없다
    never_started = make_take(db_session, user.id, status="READY")
    assert lifecycle_service.mark_ended(db_session, never_started.id) is False
    db_session.refresh(never_started)
    assert never_started.status == "READY"


def test_end_leaves_a_finished_take_and_the_fe_end_time(db_session: Session, user: User):
    """이미 끝난 Take 는 바꾸지 않는다. FE 가 먼저 적은 종료 시각도 덮지 않는다."""
    completed = make_take(db_session, user.id, status="COMPLETED")
    assert lifecycle_service.mark_ended(db_session, completed.id) is False
    db_session.refresh(completed)
    assert completed.status == "COMPLETED"

    running = make_take(db_session, user.id)
    fe_end = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
    running.ended_at = fe_end
    db_session.commit()
    assert lifecycle_service.mark_ended(db_session, running.id) is True
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
    """
    FE 종료 흐름의 Take PUT. 마이크를 못 열었거나 재연결을 포기한 FE 는 stop 없이 이것만 보낸다.
    """
    response = client.put(
        f"/api/pitches/{take.pitch_id}/takes/{take.id}",
        json={"ended_at": "2026-10-22T10:05:00Z"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.status_code


def test_put_end_while_connected_leaves_it_to_stop(
    client: TestClient, db_session: Session, take: Take, token: str, store: FakeTranscriptStore
):
    """
    연결이 붙어 있으면 그 연결의 stop 이 마지막 전사를 저장한 뒤 끝낸다. PUT 이 먼저 적지 않는다.
    """
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
    """
    끊긴 뒤 30초 안에 PUT 으로 끝내면 stop 처럼 남은 전사를 받아 두고 스트림을 정리한 뒤 끝낸다.
    """
    adapter = use(FakeSttAdapter(Plan(replies=[transcript(0, 100, "마지막", is_final=True)])))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.receive_json()
    stream = state.streams[TAKE_ID]
    deadline = time.monotonic() + 2.0
    while stream.has_client and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not stream.has_client

    assert put_end(client, take, token) == 200

    assert registry.active_count() == 0
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
    stream = registry.create_stream(
        TAKE_ID,
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(Plan(swallow_close=True)),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    state.streams[TAKE_ID] = stream
    stream.start()
    await stream.attach(tab)
    stream.push(frame(1, 0), source=tab)
    stream.end()
    stream.detach(tab)

    assert await finish.end_without_stop(TAKE_ID) is None

    stream.cancel()
    await stream.wait_closed(timeout=1.0)
    assert await finish.end_without_stop(TAKE_ID) is not None
