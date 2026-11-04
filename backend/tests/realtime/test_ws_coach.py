"""발표 중 코치와 Take 종료 — WebSocket 흐름 전체. Deepgram · 전사 저장소 · 코치 AI · 코치 저장소만
가짜고, 인증 · 인가 · Take 시작 때 고정 · 코치가 들고 있을 값 읽기는 테스트 DB 로 실제로 돈다.

계약: `docs/ai/architecture.md` 1-3 · 1-4 · 2절, `docs/ai/features/coach-agent.md` 12-1 · 12-2.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import dependencies as coach_dependencies
from pitch_coach_backend.coach import finalize as coach_finalize
from pitch_coach_backend.coach.requests import SlideEvent, TakeContext
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.pitch.entity import StandardAnalyses
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.realtime.dto import AutoEndReason
from pitch_coach_backend.realtime.stream import finish, registry, runtime, state
from pitch_coach_backend.realtime.stream.record import CoachDeps
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import UtteranceEnd
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    db_down,
    frame,
    handshake,
    make_stream,
    metrics,
    send_metrics,
    short_time_limit,
    stop,
    transcript,
    use,
    use_store,
    wait_state,
    wait_until,
)
from tests.test_coach_session import FakeClient, FakeStore, constants, reply


class Coach:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.store = FakeStore()


@pytest.fixture
def coach(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Coach:
    """코치 AI · 코치 저장소를 가짜로. conftest 의 '코치 없음' 대신 끼운다."""
    fake = Coach()
    monkeypatch.setattr(coach_dependencies, "_coach_client", fake.client)
    monkeypatch.setattr(coach_dependencies, "_coach_store", fake.store)
    return fake


@pytest.fixture(autouse=True)
def _no_leftover_finalize(monkeypatch: pytest.MonkeyPatch):
    # 테스트마다 새로 둔다. 끝난 이벤트 루프의 태스크가 남아 '이미 돌고 있다'로 보이지 않게
    monkeypatch.setattr(coach_finalize, "_running", TaskRegistry())
    yield
    for task in coach_finalize._running.tasks():
        task.cancel()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def receive_type(ws, kind: str) -> dict:
    """그 type 의 메시지가 올 때까지 읽는다."""
    while True:
        message = ws.receive_json()
        if message["type"] == kind:
            return message


def slide(ws, number: int, t_ms: int) -> None:
    ws.send_text(json.dumps({"type": "slide", "slide_number": number, "t_ms": t_ms}))


# ── 발표 중 ───────────────────────────────────────────────────────────


def test_metrics_call_the_coach_and_its_words_reach_the_screen(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore, coach: Coach
):
    coach.client.replies = [reply(t_ms=1_000, action="INTERVENE", indicators={"GAZE": "SCRIPT"})]

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        slide(ws, 1, 0)
        send_metrics(ws, 1_000)
        message = receive_type(ws, "coach")
        stop(ws)

    assert message == {
        "type": "coach",
        "t_ms": 1_000,
        "action": "INTERVENE",
        "feedback": {
            "area": "GAZE",
            "instruction": "LOOK_AT_CAMERA",
            "message": "청중을 봐 주세요",
            "priority": 80,
            "confidence": 0.9,
            "evidence": {"start_ms": -4_000, "end_ms": 1_000},
        },
        "indicators": {"GAZE": "SCRIPT"},
    }
    (request,) = coach.client.evaluated
    assert request["take_id"] == str(TAKE_ID)
    assert request["mode"] == "COACHING"
    assert request["plan"]["target_ms"] == 300_000
    assert request["inputs"]["slide"] == {"number": 1, "started_ms": 0}
    assert request["inputs"]["gaze_records"] == [metrics(1_000)["gaze"]]
    # 장 변경은 원자료로 저장한다
    assert store.slides == {0: SlideEvent(1, 0)}


def test_metrics_slide_number_does_not_decide_the_slide(
    client: TestClient, stt: FakeSttAdapter, token: str, coach: Coach
):
    """`metrics.slide_number` 는 참고값이다. 장은 `slide` 로만 정한다 (1-3 2번)."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        send_metrics(ws, 1_000, slide_number=3)
        wait_until(lambda: len(coach.client.evaluated) == 1)
        stop(ws)

    assert coach.client.evaluated[0]["inputs"]["slide"] is None


def test_buffered_metrics_are_not_sent_to_the_coach(
    client: TestClient, stt: FakeSttAdapter, token: str, store: FakeTranscriptStore, coach: Coach
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        for t in (1_000, 2_000):
            ws.send_text(json.dumps({**metrics(t), "buffered": True}))
        send_metrics(ws, 3_000)
        wait_until(lambda: len(coach.client.evaluated) == 1)
        stop(ws)

    assert [r["t_ms"] for r in coach.client.evaluated] == [3_000]
    # 몰아 온 1초는 창 · 원자료에는 들어간다
    assert len(coach.client.evaluated[0]["inputs"]["gaze_records"]) == 3
    assert sorted(store.metrics) == [1_000, 2_000, 3_000]


def test_exam_take_is_judged_but_never_spoken_to(
    client: TestClient,
    stt: FakeSttAdapter,
    token: str,
    coach: Coach,
    take: Take,
    db_session: Session,
):
    take.mode = "EXAM"
    db_session.commit()
    coach.client.replies = [reply(t_ms=1_000, action="INTERVENE")]

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        send_metrics(ws, 1_000)
        wait_until(lambda: len(coach.client.evaluated) == 1)
        closed = stop(ws)

    assert coach.client.evaluated[0]["mode"] == "EXAM"
    assert closed["state"] == "closed"


def test_coach_failing_to_load_still_streams(
    client: TestClient,
    stt: FakeSttAdapter,
    token: str,
    store: FakeTranscriptStore,
    coach: Coach,
    monkeypatch: pytest.MonkeyPatch,
):
    """코칭은 부가 기능이다. 값을 못 읽어도 STT · 원자료는 그대로 돈다."""
    from pitch_coach_backend.coach import store as coach_store

    def broken(db, take_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(coach_store, "load_take_context", broken)

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        send_metrics(ws, 1_000)
        stop(ws)

    assert coach.client.evaluated == []
    assert sorted(store.metrics) == [1_000]


def test_transient_start_failure_is_retried_before_enabling_the_coach(
    client, stt, token, user, take, db_session, coach, monkeypatch
):
    take.status = "READY"
    user.raw_data_consent = True
    db_session.add(
        StandardAnalyses(
            pitch_id=take.pitch_id,
            version=1,
            targets=[],
            excluded=[],
            meta={"schema_version": "1.0"},
            script_used=True,
        )
    )
    db_session.commit()
    real = lifecycle_service.mark_running
    calls = []

    def flaky(db, take_id):
        calls.append(take_id)
        if len(calls) == 1:
            raise db_down()
        return real(db, take_id)

    monkeypatch.setattr(lifecycle_service, "mark_running", flaky)
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, 1_000)
        wait_until(lambda: len(coach.client.evaluated) == 1)
        stop(ws)

    db_session.refresh(take)
    assert len(calls) == 2
    assert take.standard_version == 1 and take.raw_data_consent is True
    assert coach.client.evaluated[0]["script_used"] is True


# ── Take 종료 ─────────────────────────────────────────────────────────


def test_stop_ends_the_take_with_its_utterance_ends_and_starts_the_take_result(
    client: TestClient, token: str, store: FakeTranscriptStore, coach: Coach
):
    """확정 단어에는 받기 직전의 마지막 metrics 시각을 붙이고, 문장 끝 신호는 Take 를 끝낼 때 한
    번에
    쓰고, 끝낸 뒤 finalize 를 그 Take 의 마지막 창으로 부른다 (1-3 3번)."""
    use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(100, 600, "매출은", is_final=True),
                    UtteranceEnd(last_word_end_ms=600),
                ]
            )
        )
    )

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        send_metrics(ws, 1_000)
        wait_until(lambda: len(coach.client.evaluated) == 1)
        ws.send_bytes(frame(1, 0))
        receive_type(ws, "transcript")
        ws.send_bytes(frame(2, 100))
        send_metrics(ws, 2_000)
        wait_until(lambda: len(coach.client.evaluated) == 2)
        stop(ws)

    assert store.segments[0].final_at_ms == 1_000
    assert store.ended == [TAKE_ID]
    assert store.utterance_ends[TAKE_ID] == [600]
    wait_until(lambda: len(coach.client.finalized) == 1)
    (request,) = coach.client.finalized
    assert request["t_ms"] == 2_000
    assert request["coach_state"] == {"v": 2, "last_t_ms": 2_000}
    assert request["inputs"]["words"] == [{"word": "매출은", "start_ms": 100, "end_ms": 600}]
    assert request["inputs"]["utterance_ends"] == [600]
    # 코치가 실패를 돌려줬다 (가짜는 늘 None) — Take 를 FAILED 로 둔다
    wait_until(lambda: coach.store.failed == [TAKE_ID])


def test_ready_take_fixes_its_values_when_it_starts(
    client: TestClient,
    stt: FakeSttAdapter,
    token: str,
    coach: Coach,
    take: Take,
    db_session: Session,
):
    take.status = "READY"
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        send_metrics(ws, 1_000)
        stop(ws)

    db_session.refresh(take)
    # 끝난 것으로 적는 일은 가짜 전사 저장소가 해서 DB 에는 시작한 상태가 남는다
    assert take.status == "RUNNING"
    # 시작할 때 고정했다 (실은 미션이 없는 첫 Take)
    assert take.mission_ids == []
    assert take.raw_data_consent is False
    assert coach.client.evaluated[0]["missions"] == []


def test_put_end_without_stop_also_starts_the_take_result(
    client: TestClient, user, take: Take, db_session: Session, coach: Coach
):
    """STT 없이 끝낸 Take (끝내기 PUT). 끝난 것으로 적은 뒤 Take 결과를 띄운다."""
    from pitch_coach_backend.core.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    started = datetime.now(UTC)
    ended = started + timedelta(minutes=5)
    response = client.put(
        f"/api/pitches/{take.pitch_id}/takes/{take.id}",
        json={"started_at": started.isoformat(), "ended_at": ended.isoformat()},
        headers=headers,
    )

    assert response.is_success
    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "FINALIZING")
    # 이 프로세스에 그 Take 의 세션이 없다 — finalize 가 DB 에서 다시 만든다(가짜 저장소는 없음)
    wait_until(lambda: not coach_finalize._running)


@pytest.mark.anyio
async def test_auto_end_also_starts_the_take_result():
    client, coach_store = FakeClient(), FakeStore()
    store = FakeTranscriptStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(1_500))
    stream._coach = CoachDeps(client=client, store=coach_store)
    stream._record.coach.enable(TakeContext(constants()), client=client, store=coach_store)
    tab = FakeWebSocket()
    stream.start()
    await stream.attach(tab)
    stream.push_metrics(_metrics_message(1_000), source=tab)

    assert await stream.wait_closed(timeout=3.0)
    for _ in range(100):
        if client.finalized:
            break
        await asyncio.sleep(0.02)

    assert store.ended == [stream.take_id]
    assert [r["t_ms"] for r in client.finalized] == [1_000]


def _metrics_message(t_ms: int):
    from pitch_coach_backend.realtime.dto import MetricsMessage

    return MetricsMessage.model_validate(metrics(t_ms))


# ── 1차 리뷰에서 나온 경우 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_two_endings_at_once_share_one_seal():
    """자동 종료 정리와 그 뒤 온 stop 이 같이 Take 를 끝내도, 진행 중인 판단을 기다린 재료로 Take
    결과를 띄운다. 기다리지 않은 쪽이 먼저 적으면 재료 없이 replay 로 간다."""
    client, coach_store = FakeClient(), FakeStore()
    client.release.clear()
    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.enable(TakeContext(constants()), client=client, store=coach_store)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=False)
    await asyncio.sleep(0)
    deps = CoachDeps(client=client, store=coach_store)

    first = asyncio.create_task(
        finish.finish_take(TAKE_ID, store, end_reason="SILENCE", coach=deps)
    )
    second = asyncio.create_task(
        finish.finish_take(TAKE_ID, store, end_reason=None, coach=deps)
    )
    await asyncio.sleep(0.05)
    client.release.set()
    results = await asyncio.gather(first, second)
    for _ in range(50):
        if client.finalized:
            break
        await asyncio.sleep(0.02)

    assert sorted(results) == [False, True]
    (request,) = client.finalized
    assert request["coach_state"] == {"v": 2, "last_t_ms": 1_000}
    assert "replay" not in request


@pytest.mark.anyio
async def test_raw_rows_not_saved_by_the_end_are_dropped_not_retried():
    """끝낸 뒤 다시 넣으면 Take 결과 저장(동의 없는 Take 의 원자료 삭제)과 엇갈린다."""
    store = FakeTranscriptStore(metric_errors=[db_down()] * 3)
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.metrics.add(_metrics_row(1_000))

    await finish.flush_metrics(TAKE_ID)
    await asyncio.sleep(1.2)

    assert store.metrics == {}
    assert len(record.metrics) == 0
    assert record.metrics._retry is None


def _metrics_row(t_ms: int):
    from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO

    return MetricSecondCreateDTO(
        t_ms=t_ms, slide_number=None, gaze=None, voice=None, stt_status="ok"
    )


@pytest.mark.anyio
async def test_shutdown_saves_slide_events_even_without_pending_metrics():
    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    store.append_delay = 0.05
    record.slides.add(SlideEvent(2, 59_390))

    await registry.stop_all()

    assert store.slides == {59_390: SlideEvent(2, 59_390)}


@pytest.mark.parametrize("stage", ["FINALIZING", "SCORING"])
def test_review_waits_for_the_take_result_and_scoring(
    take: Take, db_session: Session, stage: str
):
    """Take 결과를 만드는 중에는 리뷰를 막아 분석 단계를 유지한다."""
    from pitch_coach_backend.module.take.service import review as review_service

    take.status = "ANALYZING"
    take.analysis_stage = stage
    db_session.commit()

    assert review_service.claim_review(db_session, take.id, uuid.uuid4()) is None

    db_session.refresh(take)
    assert take.analysis_stage == stage


def test_reconnect_does_not_read_the_coach_values_again(
    client: TestClient,
    stt: FakeSttAdapter,
    token: str,
    coach: Coach,
    monkeypatch: pytest.MonkeyPatch,
):
    from pitch_coach_backend.coach import store as coach_store

    calls: list[object] = []
    real = coach_store.load_take_context

    def counting(db, take_id):
        calls.append(take_id)
        return real(db, take_id)

    monkeypatch.setattr(coach_store, "load_take_context", counting)

    for _ in range(2):
        with client.websocket_connect(WS_PATH) as ws:
            handshake(ws, token)
            send_metrics(ws, 1_000)
            wait_until(lambda: len(coach.client.evaluated) >= 1)

    assert len(calls) == 1


@pytest.mark.anyio
async def test_audio_dropped_from_the_queue_is_not_heard():
    """큐가 넘쳐 버린 오디오는 STT 에 닿지 않았다. 그 1초를 `ok` 로 적으면 복구가 '말이 없었다' 로
    센다."""
    stream = make_stream(FakeSttAdapter(), FakeTranscriptStore(), short_time_limit(3_600_000))
    stream.state = "ok"
    # 스트림을 띄우지 않아 아무도 큐를 비우지 않는다. 오래된 프레임부터 버려진다
    for seq in range(1, runtime.QUEUE_MAX_FRAMES + 11):
        stream.push(frame(seq, (seq - 1) * 100))
    metric = _metrics_message(1_000)

    assert stream._stt_status_of(metric) == "degraded"
    stream.cancel()


# ── 2차 리뷰에서 나온 경우 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_save_failing_after_close_is_not_put_back():
    """끝낸 뒤 돌던 저장이 실패해도 되돌려 넣거나 다시 넣지 않는다."""
    from pitch_coach_backend.realtime.storage.metrics import MetricBuffer

    store = FakeTranscriptStore(metric_errors=[db_down()], append_delay=0.05)
    buffer = MetricBuffer(TAKE_ID, store)
    buffer.add(_metrics_row(1_000))
    running = asyncio.create_task(buffer.flush())
    await asyncio.sleep(0.01)

    buffer.close()

    assert await running is False
    assert len(buffer) == 0
    assert buffer._retry is None


class GatedSocket(FakeWebSocket):
    """화면 전송이 문이 열릴 때까지 걸린다."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.gate.set()

    async def send_text(self, text: str) -> None:
        await self.gate.wait()
        await super().send_text(text)


@pytest.mark.anyio
async def test_final_time_is_taken_before_the_screen_send():
    """화면에 보내는 동안 온 metrics 의 요청에는 이 단어가 없다. 그 시각을 확정 시각으로 적으면
    replay 가 그 요청에 단어를 넣어 실시간과 달라진다."""
    store = FakeTranscriptStore()
    stream = make_stream(FakeSttAdapter(), store, short_time_limit(3_600_000))
    tab = GatedSocket()
    await stream.attach(tab)
    stream.push_metrics(_metrics_message(1_000), source=tab)
    tab.gate.clear()

    final = asyncio.create_task(
        stream._on_transcript(transcript(100, 600, "매출은", is_final=True))
    )
    await asyncio.sleep(0.01)
    stream.push_metrics(_metrics_message(2_000), source=tab)
    tab.gate.set()
    await final

    assert store.segments[0].final_at_ms == 1_000
    stream.cancel()


@pytest.mark.anyio
async def test_left_take_keeps_its_utterance_ends_for_a_revival():
    """떠난 Take 를 끝낼 때 문장 끝 신호도 쓴다. 끝내기 PUT 이 되살리면 replay 가 쓴다."""
    from datetime import UTC, datetime

    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.on_utterance_end(1_500)

    await registry._end_abandoned(TAKE_ID, store, datetime.now(UTC), "ABANDONED")

    assert store.utterance_ends[TAKE_ID] == [1_500]
    assert record.coach.released
    # 놓은 뒤에는 모른다 — 끝내기 PUT 이 저장된 값을 덮지 않는다
    assert finish.utterance_ends_of(TAKE_ID) is None


@pytest.mark.anyio
async def test_ending_that_cannot_be_written_is_written_later():
    """종료 기록이 실패해도 뒤에 끝내는 쪽(끝내기 PUT 등)이 없을 수 있다. 뒤에서 다시 적고, 적히면
    Take 결과를 띄운다. 그때까지 코치의 기억(문장 끝 신호 · 재료)을 들고 있는다."""
    client, coach_store = FakeClient(), FakeStore()
    store = FakeTranscriptStore(fail_mark=True)
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.enable(TakeContext(constants()), client=client, store=coach_store)
    record.coach.on_utterance_end(1_500)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=True)
    record.coach.coach_state = {"v": 2}
    deps = CoachDeps(client=client, store=coach_store)

    assert await finish.finish_take(TAKE_ID, store, end_reason=None, coach=deps) is False
    assert not record.coach.released

    store.fail_mark = False
    for _ in range(100):
        if client.finalized:
            break
        await asyncio.sleep(0.01)

    assert store.utterance_ends[TAKE_ID] == [1_500]
    assert [r["coach_state"] for r in client.finalized] == [{"v": 2}]
    assert record.coach.released


@pytest.mark.anyio
async def test_ending_written_late_still_starts_the_take_result(monkeypatch: pytest.MonkeyPatch):
    """DB 가 늦어 기다리기를 그만둬도 적는 일은 커밋된다. 그때 Take 결과를 띄운다 — 안 띄우면 아무도
    띄우지 않아 앱이 다시 뜰 때까지 Take 결과가 없다."""
    monkeypatch.setattr(finish, "MARK_AUTO_END_TIMEOUT_SEC", 0.05)

    class SlowStore(FakeTranscriptStore):
        async def mark_ended(self, take_id, end_reason=None, utterance_ends=None) -> bool:
            await asyncio.sleep(0.2)
            return await super().mark_ended(take_id, end_reason, utterance_ends)

    client, coach_store = FakeClient(), FakeStore()
    store = SlowStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.enable(TakeContext(constants()), client=client, store=coach_store)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=False)
    await asyncio.sleep(0)
    deps = CoachDeps(client=client, store=coach_store)

    assert await finish.finish_take(TAKE_ID, store, end_reason=None, coach=deps) is False
    for _ in range(50):
        if client.finalized:
            break
        await asyncio.sleep(0.02)

    assert store.ended == [TAKE_ID]
    assert [r["t_ms"] for r in client.finalized] == [1_000]


@pytest.mark.anyio
async def test_closing_or_deleting_a_take_without_a_stream_lets_go_of_the_coach():
    store = FakeTranscriptStore()
    closed = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    closed.coach.coach_state = {"v": 2}
    other = uuid.uuid7()
    deleted = state.take_record(other, short_time_limit(60_000), store)

    await registry.end_closed_tab(TAKE_ID)
    await registry.discard([other])

    assert closed.coach.released and closed.coach.coach_state is None
    assert deleted.coach.released


@pytest.mark.anyio
async def test_left_take_saves_its_unsaved_coach_events_before_letting_go():
    """떠난 Take 도 끝내기 PUT 으로 되살아날 수 있다. 그때 Take 결과에 실리도록 저장 못 한 코치
    이벤트를 넣고 놓는다."""
    from datetime import UTC, datetime

    client, coach_store = FakeClient(), FakeStore(errors=[db_down()])
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}])
    ]
    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.enable(TakeContext(constants()), client=client, store=coach_store)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=False)
    for _ in range(5):
        await asyncio.sleep(0)
    assert coach_store.events == []

    await registry._end_abandoned(TAKE_ID, store, datetime.now(UTC), "ABANDONED")

    assert [e["event_id"] for e, _ in coach_store.events] == ["iv-1000"]
    assert record.coach.released


# ── 4차 리뷰에서 나온 경우 ────────────────────────────────────────────


def _put_end(client: TestClient, user, take: Take):
    from pitch_coach_backend.core.security import create_access_token

    return client.put(
        f"/api/pitches/{take.pitch_id}/takes/{take.id}",
        json={"ended_at": datetime.now(UTC).isoformat()},
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )


def _coach_record(store: FakeTranscriptStore, coach: Coach):
    """1초가 지난 코치 세션. 몰아 온 metrics 로 넣어 판단은 하지 않는다 — 이 스레드에는 이벤트
    루프가 없다."""
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.enable(TakeContext(constants()), client=coach.client, store=coach.store)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=True)
    record.coach.coach_state = {"v": 2}
    return record


def test_put_end_of_a_take_already_ended_lets_go_of_the_coach(
    client: TestClient,
    user,
    take: Take,
    db_session: Session,
    coach: Coach,
    store: FakeTranscriptStore,
):
    """끝내기 PUT 이 왔는데 Take 가 이미 끝나 있으면 적지 않는다. 먼저 적은 쪽이 Take 결과를
    띄웠다 — 남은 코치의 기억과 재료를 놓는다 (WS stop 과 같은 뒤처리)."""
    take.status = "ANALYZING"
    take.analysis_stage = "FINALIZING"
    db_session.commit()
    record = _coach_record(store, coach)

    assert _put_end(client, user, take).is_success

    assert record.coach.released
    assert record.seal is None
    assert coach.client.finalized == []


def test_put_end_that_cannot_be_written_is_written_later(
    client: TestClient,
    user,
    take: Take,
    coach: Coach,
    store: FakeTranscriptStore,
    monkeypatch: pytest.MonkeyPatch,
):
    """끝내기 PUT 이 Take 종료를 못 적으면 실패로 답하고(FE 는 다시 보낸다), 뒤에서도 마저 적어
    Take 결과를 띄운다."""
    from pitch_coach_backend.module.take import controller as take_controller

    def db_down(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(take_controller, "mark_ended", db_down)
    _coach_record(store, coach)

    with pytest.raises(RuntimeError):
        _put_end(client, user, take)

    wait_until(lambda: len(coach.client.finalized) == 1)
    assert store.ended == [TAKE_ID]
    assert coach.client.finalized[0]["coach_state"] == {"v": 2}


@pytest.mark.anyio
async def test_a_cut_off_ending_record_is_written_again():
    """먼저 시작한 종료 기록이 끊겼으면 실패와 같이 다시 적는다. 끊긴 기록의 결과를 꺼내다
    오류를 내고 멈추지 않는다."""
    client, coach_store = FakeClient(), FakeStore()
    store = FakeTranscriptStore()
    state.take_record(TAKE_ID, short_time_limit(60_000), store)
    cut = asyncio.get_running_loop().create_future()
    cut.cancel()
    ending = finish.Ending(end_reason=None, utterance_ends=[1_500], snapshot=None)

    finish.mark_later(
        TAKE_ID, store, ending, CoachDeps(client=client, store=coach_store), marking=cut
    )
    for _ in range(100):
        if store.ended:
            break
        await asyncio.sleep(0.01)

    assert store.ended == [TAKE_ID]
    assert store.utterance_ends[TAKE_ID] == [1_500]


@pytest.mark.anyio
async def test_shutdown_waits_for_a_whole_take_ending(monkeypatch: pytest.MonkeyPatch):
    """앱을 내릴 때 Take 끝내기 정리(마지막 조각 기다리기 · 재료와 원자료 저장 · DB 에 적기 ·
    닫기)를 끝까지 기다린다. 중간에 끊으면 Take 가 진행 중으로 남는다."""
    for module, name in (
        (runtime, "FINAL_METRICS_WAIT_SEC"),
        (finish, "FLUSH_METRICS_TIMEOUT_SEC"),
        (finish, "MARK_AUTO_END_TIMEOUT_SEC"),
        (stt_loop, "ABORT_TIMEOUT_SEC"),
    ):
        monkeypatch.setattr(module, name, 0.1)
    monkeypatch.setattr(registry, "SEAL_WAIT_SEC", 0.05)
    finished = asyncio.Event()

    async def ending() -> None:
        # 적기와 닫기만 기다리던 예전 상한(0.2초)보다 길다
        await asyncio.sleep(0.3)
        finished.set()

    state.spawn(ending(), name="take-auto-end:test")
    await registry.stop_all()

    assert finished.is_set()


# ── 5차 리뷰에서 나온 경우 ────────────────────────────────────────────


def test_put_end_while_an_auto_end_is_written_keeps_its_reason(
    client: TestClient, user, take: Take, db_session: Session, coach: Coach
):
    """자동 종료의 기록이 늦어 뒤에서 다시 적는 동안 FE 의 끝내기 PUT 이 먼저 적어도 자동 종료
    이유가 남는다."""
    state.ended[TAKE_ID] = AutoEndReason.SILENCE

    assert _put_end(client, user, take).is_success

    db_session.refresh(take)
    assert (take.status, take.end_reason) == ("ANALYZING", "SILENCE")
    assert state.ended[TAKE_ID] == AutoEndReason.SILENCE


@pytest.mark.anyio
async def test_shutdown_writes_down_the_utterance_ends_of_live_takes():
    """문장 끝 신호는 Take 를 끝낼 때 한 번에 적는다. 앱을 내리면 진행 중인 Take 의 것은 지금
    적어 둔다 — 다시 뜬 프로세스가 이어 받는다."""
    store = FakeTranscriptStore()
    live = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    live.coach.on_utterance_end(1_500)
    ended_id = uuid.uuid7()
    ended = state.take_record(ended_id, short_time_limit(60_000), store)
    ended.coach.on_utterance_end(900)
    ended.coach.release()

    await registry.stop_all()

    assert store.saved_utterance_ends == {TAKE_ID: [1_500]}


def test_a_take_picked_up_after_a_restart_keeps_its_utterance_ends(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    """앱이 Take 도중에 다시 떴다. Take 를 끝낼 때 메모리의 목록으로 덮어 쓰므로 앞 프로세스가
    적어 둔 문장 끝 신호를 이어 받아야 앞부분이 빠지지 않는다."""
    store = use_store(FakeTranscriptStore(stored_utterance_ends={TAKE_ID: [1_500, 9_000]}))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        stop(ws)

    assert store.utterance_ends[TAKE_ID] == [1_500, 9_000]


@pytest.mark.anyio
async def test_one_late_ending_record_per_take():
    """끝내기 PUT 을 여러 번 다시 보내도 뒤에서 다시 적는 작업은 하나다."""

    class Slow(FakeTranscriptStore):
        async def mark_ended(self, take_id, end_reason=None, utterance_ends=None) -> bool:
            self.calls.append("try")
            await asyncio.sleep(0.05)
            return await super().mark_ended(take_id, end_reason, utterance_ends)

    store = Slow()
    ending = finish.Ending(end_reason=None, utterance_ends=None, snapshot=None)
    for _ in range(3):
        finish.mark_later(TAKE_ID, store, ending, None)
    for _ in range(50):
        if store.ended:
            break
        await asyncio.sleep(0.01)

    assert store.calls.count("try") == 1
    assert store.ended == [TAKE_ID]


@pytest.mark.anyio
async def test_ending_record_that_retrying_cannot_fix_is_given_up_at_once():
    """값이 맞지 않는 것 같은 실패는 다시 해도 같다. 몇 분씩 다시 적지 않는다. 코치의 기억은 둔다
    — 뒤에 끝내기 PUT 이 오면 그 기억(문장 끝 신호 · 재료)으로 적는다."""
    from sqlalchemy.exc import DataError

    class Broken(FakeTranscriptStore):
        async def mark_ended(self, take_id, end_reason=None, utterance_ends=None) -> bool:
            self.calls.append("try")
            raise DataError("UPDATE takes", {}, ValueError("bad value"))

    store = Broken()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    ending = finish.Ending(end_reason=None, utterance_ends=None, snapshot=None)

    finish.mark_later(TAKE_ID, store, ending, None)
    for _ in range(50):
        if not state.background.running(("mark", TAKE_ID)):
            break
        await asyncio.sleep(0.01)

    assert store.calls == ["try"]
    assert not record.coach.released


@pytest.mark.anyio
async def test_left_take_saves_its_raw_seconds_and_closes_the_buffers():
    """떠난 Take 도 끝내기 PUT 으로 되살아날 수 있다. 남은 1초 원자료를 넣고 버퍼를 닫는다 — 닫지
    않으면 다시 넣기 타이머가 FAILED 로 끝난 Take 에 계속 돈다."""
    from datetime import UTC, datetime

    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.metrics.add(_metrics_row(1_000))

    await registry._end_abandoned(TAKE_ID, store, datetime.now(UTC), "ABANDONED")

    assert list(store.metrics) == [1_000]
    record.metrics.add(_metrics_row(2_000))
    assert len(record.metrics) == 0


# ── 6차 리뷰에서 나온 경우 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_rows_that_can_never_be_saved_do_not_block_the_rest():
    """값이 칸에 맞지 않는 것 같은 실패는 다시 넣어도 같다. 들고 있으면 뒤에 온 행까지 그 뒤에
    막히므로 그 묶음만 버리고 다음 행을 넣는다."""
    from sqlalchemy.exc import DataError

    from pitch_coach_backend.realtime.storage.metrics import MetricBuffer

    bad = DataError("INSERT take_metric_seconds", {}, ValueError("out of range"))
    store = FakeTranscriptStore(metric_errors=[bad])
    buffer = MetricBuffer(TAKE_ID, store)
    buffer.add(_metrics_row(1_000))
    first = await buffer.flush()
    buffer.add(_metrics_row(2_000))
    second = await buffer.flush()

    assert (first, second) == (False, True)
    assert list(store.metrics) == [2_000]
    assert buffer._retry is None


@pytest.mark.anyio
async def test_ending_closes_the_raw_buffers_and_the_coach_together():
    """원자료 버퍼와 코치가 같은 순간에 받기를 멈춘다. 끝내는 중에 온 1초가 한쪽에만 들지 않게."""
    store = FakeTranscriptStore()
    record = state.take_record(TAKE_ID, short_time_limit(60_000), store)
    record.coach.on_metrics(1_000, gaze=None, voice=None, stt_status="ok", buffered=True)

    ending = asyncio.create_task(
        finish.finish_take(TAKE_ID, store, end_reason=None, coach=None)
    )
    await asyncio.sleep(0)
    record.metrics.add(_metrics_row(2_000))
    record.coach.on_metrics(2_000, gaze=None, voice=None, stt_status="ok", buffered=True)
    await ending

    assert 2_000 not in store.metrics
    assert record.coach.clock_ms == 1_000
