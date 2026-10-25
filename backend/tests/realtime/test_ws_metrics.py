"""1초 원자료(metrics) — 묶음 저장 · 재시도 · 종료 전 저장."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO
from pitch_coach_backend.module.take.entity import Take, TakeMetricSecond
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import record as record_service
from pitch_coach_backend.realtime.dto import (
    MetricsMessage,
)
from pitch_coach_backend.realtime.storage.metrics import METRICS_BATCH, MetricBuffer
from pitch_coach_backend.realtime.stream import runtime as take_stream
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt.adapter import (
    SttConfig,
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
    closed_with,
    db_down,
    empty_final,
    frame,
    handshake,
    make_stream,
    metrics,
    receive_auto_end,
    row,
    send_metrics,
    short_time_limit,
    stop,
    use,
    use_policy,
    use_store,
    wait_state,
    wait_until,
)

# ── 1초 원자료 (metrics) ──────────────────────────────────────────────


def test_metrics_are_saved_every_few_seconds_with_the_stt_state(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore
):
    """1초에 하나씩 오는 측정값은 METRICS_BATCH 개가 모일 때마다 한 번에 저장한다. 시선 · 음량
    기록은 받은 그대로 두고, 그 1초의 STT 상태를 같이 남긴다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(METRICS_BATCH):
            ws.send_bytes(frame(i + 1, i * 1000, duration_ms=1000))
        send_metrics(ws, *range(1000, METRICS_BATCH * 1000, 1000))
        time.sleep(0.1)
        assert store.metrics == {}
        send_metrics(ws, METRICS_BATCH * 1000)
        wait_until(lambda: len(store.metrics) == METRICS_BATCH)
        assert store.calls == ["append_metrics"]
        stop(ws)

    sent = metrics(1000)
    assert store.metrics[1000] == MetricSecondCreateDTO(
        t_ms=1000,
        slide_number=1,
        gaze=sent["gaze"],
        voice=sent["voice"],
        stt_status="ok",
    )


def test_stop_saves_the_last_metrics_before_ending_the_take(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore
):
    """FE 는 마지막 1초 조각을 stop 앞에 보낸다. Take 뒤 분석이 원자료를 읽으므로 모자란 묶음도
    저장한 뒤에 끝난 것으로 적는다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, 1000)
        ws.send_text(json.dumps(metrics(1400, duration_ms=400)))
        stop(ws)

    assert sorted(store.metrics) == [1000, 1400]
    assert store.metrics[1400].voice == {
        "t_ms": 1000,
        "duration_ms": 400,
        "level_db": -31.2,
        "audio_live": True,
    }
    assert store.calls == ["append_metrics", "mark_ended"]


def test_metrics_resent_after_a_reconnect_keep_the_first_save(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore
):
    """끊기면 그때까지 모은 것을 저장해 둔다. FE 는 끊긴 동안 쌓아 둔 것을 다시 붙은 뒤 보내서
    같은 1초가 또 온다 — 먼저 저장한 것을 둔다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, 1000, 2000)
    wait_until(lambda: sorted(store.metrics) == [1000, 2000])

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        send_metrics(ws, 2000, 3000, slide_number=2)
        stop(ws)

    assert sorted(store.metrics) == [1000, 2000, 3000]
    assert store.metrics[2000].slide_number == 1
    assert store.metrics[3000].slide_number == 2


def test_metrics_without_audio_heard_are_not_stored_as_stt_ok(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore
):
    """STT 가 ok 여도 그 1초의 오디오가 끊김 없이 들어오지 않았으면 ok 로 적지 않는다. FE 는 끊긴
    동안 쌓아 둔 1초를 다시 붙은 뒤 보내는데, 받았을 때의 상태로 적으면 복구가 STT 가 듣지 못한
    구간을 '말이 없었다' 로 센다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0, duration_ms=1000))
        ws.send_bytes(frame(2, 1000, duration_ms=1000))
        send_metrics(ws, 1000, 2000)
        # 오디오가 멈춘 뒤의 1초
        send_metrics(ws, 4000)
        # 무음으로 메우지 못할 만큼 끊긴 뒤 오디오가 다시 온다. 끊긴 동안의 1초는 그 뒤에 온다
        ws.send_bytes(frame(3, 20_000, duration_ms=1000))
        send_metrics(ws, 15_000, 21_000)
        stop(ws)

    assert {t: m.stt_status for t, m in store.metrics.items()} == {
        1000: "ok",
        2000: "ok",
        4000: "degraded",
        15_000: "degraded",
        21_000: "ok",
    }


def test_failed_metrics_save_is_retried_before_ending(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    """DB 가 잠깐 실패해도 버리지 않는다. 들고 있다가 끝낼 때 다시 넣는다."""
    store = use_store(FakeTranscriptStore(metric_errors=[db_down()]))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, *range(1000, (METRICS_BATCH + 1) * 1000, 1000))
        wait_until(lambda: not store.metric_errors)
        stop(ws)

    assert sorted(store.metrics) == list(range(1000, (METRICS_BATCH + 1) * 1000, 1000))
    assert store.calls == ["append_metrics", "mark_ended"]


def test_auto_end_saves_the_metrics_before_ending(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """자동 종료도 사용자가 끝낼 때처럼 남은 원자료를 저장한 뒤 끝난 것으로 적는다."""
    use_policy(monkeypatch, SHORT_SILENCE)
    use(
        FakeSttAdapter(
            Plan(replies=[empty_final(0, 100), empty_final(100, 200), empty_final(200, 300)])
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, 1000)
        time.sleep(0.05)
        for i in range(3):
            ws.send_bytes(frame(i + 1, i * 100))
        receive_auto_end(ws)
        wait_state(ws, "closed")

    assert sorted(store.metrics) == [1000]
    assert store.calls.index("append_metrics") < store.calls.index("mark_ended")
    assert store.auto_ended == [(TAKE_ID, "SILENCE")]


def test_auto_end_waits_for_the_last_metrics_sent_after_the_drain(
    client: TestClient, token: str, store: FakeTranscriptStore, monkeypatch: pytest.MonkeyPatch
):
    """FE 는 auto_end 를 받고 마지막 1초 조각과 stop 을 보낸다. Deepgram 정리가 먼저 끝났어도 그
    조각을 받아 저장한 뒤에 끝난 것으로 적는다."""
    use_policy(monkeypatch, SHORT_SILENCE)
    monkeypatch.setattr(take_stream, "FINAL_METRICS_WAIT_SEC", 2.0)
    use(
        FakeSttAdapter(
            Plan(replies=[empty_final(0, 100), empty_final(100, 200), empty_final(200, 300)])
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(3):
            ws.send_bytes(frame(i + 1, i * 100))
        receive_auto_end(ws)
        wait_until(lambda: TAKE_ID not in take_stream._streams)
        ws.send_text(json.dumps(metrics(300, duration_ms=300)))
        stop(ws)

    assert sorted(store.metrics) == [300]
    # 스트림이 정리(closed)된 뒤에 왔어도 정리 전 상태로 적는다. closed 로 적으면 복구가 그 1초의
    # 단어를 세지 않는다 (coach-agent.md 5-2 마지막 창)
    assert store.metrics[300].stt_status == "ok"
    assert store.calls.index("append_metrics") < store.calls.index("mark_ended")
    assert store.auto_ended == [(TAKE_ID, "SILENCE")]


@pytest.mark.anyio
async def test_audio_dropped_from_the_queue_is_not_heard():
    """큐가 넘쳐 버린 오디오는 STT 에 닿지 않았다. 그 1초를 `ok` 로 적으면 복구가 '말이 없었다' 로
    센다."""
    stream = make_stream(FakeSttAdapter(), FakeTranscriptStore(), short_time_limit(3_600_000))
    stream.state = "ok"
    # 스트림을 띄우지 않아 아무도 큐를 비우지 않는다. 오래된 프레임부터 버려진다
    for seq in range(1, take_stream.QUEUE_MAX_FRAMES + 11):
        stream.push(frame(seq, (seq - 1) * 100))

    assert stream._stt_status_of(MetricsMessage.model_validate(metrics(1_000))) == "degraded"
    stream.cancel()


@pytest.mark.parametrize(
    "message",
    [
        {**metrics(1000), "t_ms": -1},
        {**metrics(1000), "t_ms": 3_660_001},
        {**metrics(1000), "slide_number": 0},
        {**metrics(1000), "slide_number": 2**31},
        {**metrics(1000), "gaze": {"t_ms": 0, "duration_ms": 1000, "pad": "x" * 5000}},
        {**metrics(1000), "gaze": {"duration_ms": 1000}},
        {**metrics(1000), "voice": {"t_ms": 0, "duration_ms": 1000, "level_db": float("nan")}},
        {**metrics(1000), "gaze": {"t_ms": 3_600_000, "duration_ms": 1000, "state": "BOTTOM"}},
        {"type": "metrics", "t_ms": 1000},
    ],
    ids=[
        "음수 시각",
        "1시간 넘음",
        "0번 장",
        "int4 넘는 장",
        "너무 큰 기록",
        "시작 시각 없음",
        "NaN",
        "끝 시각이 안 맞는 기록",
        "칸 빠짐",
    ],
)
def test_bad_metrics_is_an_error_not_a_close(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore, message: dict
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_text(json.dumps(message))
        assert ws.receive_json()["code"] == "BAD_MESSAGE"
        assert stop(ws)["state"] == "closed"

    assert store.metrics == {}


def test_metrics_before_auth_is_rejected(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text(json.dumps(metrics(1000)))
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008


@pytest.mark.anyio
async def test_metrics_from_an_evicted_tab_are_dropped():
    """다른 탭이 이어받은 뒤 쫓겨난 탭이 보낸 것은 버린다 (오디오와 같은 규칙)."""
    store = FakeTranscriptStore()
    stream = TakeStream(
        TAKE_ID,
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=store,
    )
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    await stream.attach(new_tab)

    stream.push_metrics(MetricsMessage.model_validate(metrics(1000)), source=old_tab)
    stream.push_metrics(MetricsMessage.model_validate(metrics(2000)), source=new_tab)
    await take_stream.flush_metrics(TAKE_ID)

    assert sorted(store.metrics) == [2000]


@pytest.mark.anyio
async def test_metric_buffer_drops_the_oldest_when_saves_keep_failing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pitch_coach_backend.realtime.storage.metrics.MAX_UNSAVED_METRICS", 3)
    store = FakeTranscriptStore(metric_errors=[db_down()] * 10)
    buffer = MetricBuffer(TAKE_ID, store)
    for t in (1000, 2000, 3000, 4000):
        buffer.add(row(t))

    assert await buffer.flush() is False
    store.metric_errors.clear()
    assert await buffer.flush() is True
    assert sorted(store.metrics) == [2000, 3000, 4000]


@pytest.mark.anyio
async def test_metric_buffer_waits_before_retrying_a_failed_save(monkeypatch: pytest.MonkeyPatch):
    """저장에 실패하면 행이 더 와도 바로 다시 넣지 않는다. 기다렸다가 한 번에 넣는다."""
    monkeypatch.setattr("pitch_coach_backend.core.row_buffer.RETRY_DELAYS_SEC", (0.1,))
    store = FakeTranscriptStore(metric_errors=[db_down()] * 10)
    buffer = MetricBuffer(TAKE_ID, store)
    for t in range(1000, (METRICS_BATCH + 3) * 1000, 1000):
        buffer.add(row(t))
        await asyncio.sleep(0.01)

    assert len(store.metric_errors) == 9
    store.metric_errors.clear()
    await _wait_for(lambda: len(store.metrics) == METRICS_BATCH + 2)


@pytest.mark.anyio
async def test_stop_all_waits_for_a_save_already_running(monkeypatch: pytest.MonkeyPatch):
    """스트림이 닫히며 시작한 백그라운드 저장은 행을 꺼내 가 버퍼가 비어 보인다. 앱을 내릴 때
    그 저장도 기다린다 — 안 기다리면 진행 중인 Take 의 마지막 몇 초가 사라진다."""
    store = FakeTranscriptStore(append_delay=0.1)
    record = take_stream._take_record(TAKE_ID, SHORT_SILENCE, store)
    record.metrics.add(row(1000))
    record.metrics.flush_soon()
    await asyncio.sleep(0)
    assert len(record.metrics) == 0

    await take_stream.stop_all()

    assert sorted(store.metrics) == [1000]


@pytest.mark.anyio
async def test_metric_buffer_stops_when_the_take_is_deleted():
    store = FakeTranscriptStore(metric_errors=[NonExistentTake()])
    buffer = MetricBuffer(TAKE_ID, store)
    buffer.add(row(1000))

    assert await buffer.flush() is False
    buffer.add(row(2000))
    assert len(buffer) == 0
    assert store.metrics == {}


def test_db_keeps_the_first_save_of_the_same_second(db_session: Session, take: Take):
    """같은 1초가 또 오면 오류 없이 건너뛴다. 없는 Take 는 'Take 가 없다' 로 알린다."""
    record_service.append_metric_seconds(db_session, TAKE_ID, [row(1000), row(2000)])
    record_service.append_metric_seconds(
        db_session, TAKE_ID, [row(2000, slide_number=2), row(3000, slide_number=2)]
    )

    rows = db_session.scalars(
        select(TakeMetricSecond)
        .where(TakeMetricSecond.take_id == TAKE_ID)
        .order_by(TakeMetricSecond.t_ms)
    ).all()
    assert [(r.t_ms, r.slide_number, r.stt_status) for r in rows] == [
        (1000, 1, "ok"),
        (2000, 1, "ok"),
        (3000, 2, "ok"),
    ]
    with pytest.raises(NonExistentTake):
        record_service.append_metric_seconds(db_session, uuid.uuid7(), [row(1000)])
