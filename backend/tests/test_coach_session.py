"""코치 session 실행부의 실패·복구 회귀 테스트."""

import asyncio
from typing import Any

import pytest

from pitch_coach_backend.coach import session as session_module
from pitch_coach_backend.coach.client import (
    EvaluateReply,
)
from pitch_coach_backend.coach.requests import (
    MetricSecond,
    SlideEvent,
    TakeContext,
)
from pitch_coach_backend.coach.session import CoachSession
from pitch_coach_backend.module.take.exception import NonExistentTake
from tests.coach_fakes import (
    META,
    TAKE_ID,
    FakeClient,
    FakeStore,
    constants,
    db_down,
    gaze,
    reply,
    session,
    settle,
    tick,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_each_metrics_calls_evaluate_with_the_window_and_the_last_state():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store, slides=[SlideEvent(1, 0), SlideEvent(2, 1_500)])

    tick(coach, 1_000)
    await settle()
    final_at = coach.on_final([("매출은", 1_100, 1_600)])
    tick(coach, 2_000)
    await settle()

    first, second = client.evaluated
    assert first["t_ms"] == 1_000
    assert first["coach_state"] is None
    assert first["inputs"]["gaze_records"] == [gaze(0)]
    assert first["inputs"]["slide"] == {"number": 1, "started_ms": 0}
    assert second["coach_state"] == {"v": 2, "last_t_ms": 1_000}
    assert second["inputs"]["slide"] == {"number": 2, "started_ms": 1_500}
    assert second["inputs"]["words"] == [{"word": "매출은", "start_ms": 1_100, "end_ms": 1_600}]
    assert second["inputs"]["stt_status"] == "ok"
    # 확정 결과는 받기 직전의 마지막 metrics 시각을 받고, 다음 요청부터 실린다 (12-2)
    assert final_at == 1_000

@pytest.mark.anyio
async def test_words_finalized_before_any_metrics_have_time_zero():
    coach = CoachSession(TAKE_ID)

    assert coach.on_final([("안녕하세요", 0, 500)]) == 0

@pytest.mark.anyio
async def test_only_one_request_at_a_time_and_the_skipped_second_is_not_queued():
    client, store = FakeClient(), FakeStore()
    client.release.clear()
    coach = session(client, store)

    tick(coach, 1_000)
    await settle()
    tick(coach, 2_000)
    tick(coach, 3_000)
    client.release.set()
    await settle()
    tick(coach, 4_000)
    await settle()

    assert [r["t_ms"] for r in client.evaluated] == [1_000, 4_000]
    assert coach.skipped == 2
    # 건너뛴 1초의 기록은 다음 요청의 창에 실린다
    assert [g["t_ms"] for g in client.evaluated[1]["inputs"]["gaze_records"]] == [
        0,
        1_000,
        2_000,
        3_000,
    ]

@pytest.mark.anyio
async def test_buffered_metrics_go_to_the_window_only():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)

    for t in (1_000, 2_000, 3_000):
        tick(coach, t, buffered=True)
    await settle()
    tick(coach, 4_000)
    await settle()

    assert [r["t_ms"] for r in client.evaluated] == [4_000]
    assert len(client.evaluated[0]["inputs"]["gaze_records"]) == 4

@pytest.mark.anyio
async def test_disabled_coach_still_collects_for_the_end():
    """Take 를 시작할 때 값을 못 읽었으면 코칭은 끈다. 창 · 확정 시각 · 문장 끝은 모은다."""
    coach = CoachSession(TAKE_ID)

    tick(coach, 1_000)
    coach.on_utterance_end(900)
    snapshot = await coach.seal()

    assert snapshot is not None
    assert snapshot.constants is None and snapshot.coach_state is None
    assert snapshot.t_ms == 1_000
    assert coach.utterance_ends == [900]

@pytest.mark.anyio
@pytest.mark.parametrize(
    "replies, sent",
    [
        # 말을 걸면 보낸다
        ([reply(t_ms=1_000, action="INTERVENE")], [1_000]),
        # 상태 표시가 바뀌면 보내고, 같으면 다시 보내지 않는다
        (
            [
                reply(t_ms=1_000, indicators={"GAZE": "SCRIPT"}),
                reply(t_ms=2_000, indicators={"GAZE": "SCRIPT"}),
                reply(t_ms=3_000, indicators={"GAZE": "CAMERA"}),
            ],
            [1_000, 3_000],
        ),
        # 빈 indicators 는 '바뀌지 않음'이다
        ([reply(t_ms=1_000, indicators={"GAZE": "SCRIPT"}), reply(t_ms=2_000)], [1_000]),
    ],
)
async def test_coach_message_goes_out_on_intervention_or_changed_indicators(replies, sent):
    client, store = FakeClient(), FakeStore()
    client.replies = list(replies)
    coach = session(client, store)
    notified: list[tuple[int, EvaluateReply]] = []

    async def notify(t_ms: int, r: EvaluateReply) -> None:
        notified.append((t_ms, r))

    coach.notify = notify
    for t in range(1_000, 1_000 * (len(replies) + 1), 1_000):
        tick(coach, t)
        await settle()

    assert [t for t, _ in notified] == sent

@pytest.mark.anyio
async def test_exam_mode_never_sends_coach_messages():
    client, store = FakeClient(), FakeStore()
    client.replies = [reply(t_ms=1_000, action="INTERVENE", indicators={"GAZE": "SCRIPT"})]
    coach = session(client, store, mode="EXAM")
    notified: list[int] = []

    async def notify(t_ms: int, r: EvaluateReply) -> None:
        notified.append(t_ms)

    coach.notify = notify
    tick(coach, 1_000)
    await settle()

    assert notified == []
    assert client.evaluated[0]["mode"] == "EXAM"

@pytest.mark.anyio
async def test_events_are_saved_in_the_received_order():
    client, store = FakeClient(), FakeStore()
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}]),
        reply(t_ms=2_000, events=[{"kind": "OUTCOME", "event_id": "oc-iv-1000", "t_ms": 2_000}]),
    ]
    coach = session(client, store)

    tick(coach, 1_000)
    await settle()
    tick(coach, 2_000)
    await settle()
    await settle()

    assert [e["event_id"] for e, _ in store.events] == ["iv-1000", "oc-iv-1000"]
    assert all(meta == META for _, meta in store.events)

@pytest.mark.anyio
async def test_failed_event_save_is_kept_and_put_in_by_the_seal():
    client, store = FakeClient(), FakeStore(errors=[db_down()])
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}])
    ]
    coach = session(client, store)

    tick(coach, 1_000)
    await settle()
    await settle()
    assert store.events == []

    snapshot = await coach.seal()

    assert [e["event_id"] for e, _ in store.events] == ["iv-1000"]
    assert snapshot is not None
    assert [e["event_id"] for e, _ in snapshot.events] == ["iv-1000"]

@pytest.mark.anyio
async def test_events_of_a_deleted_take_are_dropped():
    client, store = FakeClient(), FakeStore(errors=[NonExistentTake()])
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}])
    ]
    coach = session(client, store)

    tick(coach, 1_000)
    await settle()
    await settle()
    await coach.seal()

    assert store.events == []

@pytest.mark.anyio
async def test_seal_waits_for_the_running_evaluate_and_keeps_its_state():
    client, store = FakeClient(), FakeStore()
    client.release.clear()
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()

    sealing = asyncio.create_task(coach.seal())
    await settle()
    client.release.set()
    snapshot = await sealing

    assert snapshot is not None
    assert snapshot.coach_state == {"v": 2, "last_t_ms": 1_000}

@pytest.mark.anyio
async def test_after_stop_no_new_evaluate_but_the_last_window_still_fills():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()

    coach.stop()
    # stop 앞에 FE 가 보낸 마지막 1초 조각과 STT 를 닫은 뒤의 마지막 확정 단어
    coach.on_metrics(
        1_400, gaze=gaze(1_000) | {"duration_ms": 400}, voice=None, stt_status="ok", buffered=False
    )
    coach.on_final([("끝", 1_100, 1_350)])
    coach.on_utterance_end(1_350)
    snapshot = await coach.seal()

    assert [r["t_ms"] for r in client.evaluated] == [1_000]
    assert snapshot is not None
    assert snapshot.t_ms == 1_400
    assert snapshot.inputs["words"] == [{"word": "끝", "start_ms": 1_100, "end_ms": 1_350}]
    assert snapshot.inputs["utterance_ends"] == [1_350]
    assert snapshot.inputs["gaze_records"][-1]["duration_ms"] == 400

@pytest.mark.anyio
async def test_last_window_never_says_closed():
    """자동 종료 뒤 마지막 1초 조각은 STT 를 정리한 뒤에 온다. `closed` 를 실으면 마지막 단어를
    세지 못한다 (5-2 finalize `inputs`)."""
    coach = session(FakeClient(), FakeStore())
    tick(coach, 1_000)
    coach.stop()
    tick(coach, 1_500, stt_status="closed")

    snapshot = await coach.seal()

    assert snapshot is not None
    assert snapshot.inputs["stt_status"] == "ok"

@pytest.mark.anyio
async def test_no_metrics_means_no_snapshot():
    assert await session(FakeClient(), FakeStore()).seal() is None

@pytest.mark.anyio
async def test_old_records_are_dropped_from_the_window():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    coach.on_final([("처음", 0, 500)])

    for t in range(1_000, 100_001, 1_000):
        tick(coach, t)
        await settle()

    last = client.evaluated[-1]["inputs"]
    assert last["gaze_records"][0]["t_ms"] == 100_000 - 30_000
    assert last["words"] == []
    assert len(coach._windows.gaze) < 40

@pytest.mark.anyio
async def test_late_evaluate_after_stop_keeps_its_state_but_says_nothing():
    """stop 뒤에 도착한 응답은 상태 · 이벤트만 반영한다. 끝난 발표에 말을 걸지 않는다."""
    client, store = FakeClient(), FakeStore()
    client.release.clear()
    client.replies = [reply(t_ms=1_000, action="INTERVENE")]
    coach = session(client, store)
    notified: list[int] = []

    async def notify(t_ms: int, r: EvaluateReply) -> None:
        notified.append(t_ms)

    coach.notify = notify
    tick(coach, 1_000)
    await settle()
    coach.stop()
    client.release.set()
    snapshot = await coach.seal()

    assert notified == []
    assert snapshot is not None and snapshot.coach_state == {"v": 2, "last_t_ms": 1_000}

@pytest.mark.anyio
async def test_released_session_takes_nothing_back():
    """Take 가 끝나 기억을 놓은 뒤(지움 · 떠남) 늦게 온 응답 · 끝내기가 기억을 되살리지 않는다."""
    client, store = FakeClient(), FakeStore()
    client.release.clear()
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()

    coach.release()
    client.release.set()
    await settle()

    assert coach.coach_state is None
    # 되살린 Take 를 끝낼 때: 빈 창으로 재료를 만들지 않는다 — finalize 가 DB 에서 다시 만든다
    assert await coach.seal() is None

@pytest.mark.anyio
async def test_shutdown_puts_in_the_events_it_could_not_save():
    client, store = FakeClient(), FakeStore(errors=[db_down()])
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}])
    ]
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()
    await settle()
    assert store.events == []

    await coach.shutdown(1.0)

    assert [e["event_id"] for e, _ in store.events] == ["iv-1000"]

@pytest.mark.anyio
async def test_duplicate_metrics_keep_the_first_whole_message():
    """DB 와 같이 바깥 t_ms 로 중복을 가린다. 센서가 null 이던 초도 첫 메시지를 유지한다."""
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)

    coach.on_metrics(2_000, gaze=None, voice=None, stt_status="degraded", buffered=True)
    coach.on_metrics(2_000, gaze=gaze(1_000), voice=None, stt_status="ok", buffered=True)
    snapshot = await coach.seal()
    assert snapshot is not None
    assert snapshot.inputs["gaze_records"] == []
    assert snapshot.inputs["stt_status"] == "degraded"
    assert client.evaluated == []

@pytest.mark.anyio
async def test_different_metric_ends_preserve_fractional_sensor_starts():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    coach.on_metrics(2_000, gaze=gaze(1_000), voice=None, stt_status="ok", buffered=True)
    coach.on_metrics(
        2_001,
        gaze={**gaze(1_000), "t_ms": 1_000.4, "duration_ms": 1000.6},
        voice=None,
        stt_status="ok",
        buffered=True,
    )
    tick(coach, 3_000)
    await settle()
    starts = [g["t_ms"] for g in client.evaluated[0]["inputs"]["gaze_records"]]
    assert starts == [1_000, 1_000.4, 2_000]

@pytest.mark.anyio
async def test_restart_keeps_the_first_saved_metrics_when_the_client_resends():
    client, store = FakeClient(), FakeStore()
    coach = CoachSession(TAKE_ID)
    coach.enable(
        TakeContext(constants(), seconds=(MetricSecond(1_000, 1, None, None, "degraded"),)),
        client=client,
        store=store,
    )
    tick(coach, 1_000, buffered=True)
    snapshot = await coach.seal()
    assert snapshot is not None
    assert snapshot.inputs["gaze_records"] == []
    assert snapshot.inputs["voice_records"] == []
    assert snapshot.inputs["stt_status"] == "degraded"
    assert client.evaluated == []

@pytest.mark.anyio
@pytest.mark.parametrize("resend_before_enable", [False, True])
async def test_late_context_restores_saved_metrics_and_keeps_new_inputs(resend_before_enable):
    client, store = FakeClient(), FakeStore()
    coach = CoachSession(TAKE_ID)
    tick(coach, 2_000, buffered=True)
    coach.on_final([("새 단어", 1_000, 1_500)])
    coach.on_utterance_end(1_500)
    if resend_before_enable:
        tick(coach, 1_000, buffered=True)

    coach.enable(
        TakeContext(constants(), seconds=(MetricSecond(1_000, 1, None, None, "degraded"),)),
        client=client,
        store=store,
    )
    tick(coach, 1_000, buffered=True)
    snap = await coach.seal()

    assert snap is not None and snap.t_ms == 2_000
    assert [g["t_ms"] for g in snap.inputs["gaze_records"]] == [1_000]
    assert [v["t_ms"] for v in snap.inputs["voice_records"]] == [1_000]
    assert snap.inputs["stt_status"] == "ok"
    assert snap.inputs["words"][0]["word"] == "새 단어"
    assert snap.inputs["utterance_ends"] == [1_500]
    assert client.evaluated == []

@pytest.mark.anyio
async def test_late_context_replaces_a_conflicting_latest_message_with_the_saved_nulls():
    client, store = FakeClient(), FakeStore()
    coach = CoachSession(TAKE_ID)
    tick(coach, 2_000, buffered=True)
    coach.enable(
        TakeContext(constants(), seconds=(MetricSecond(2_000, 1, None, None, "degraded"),)),
        client=client,
        store=store,
    )
    snap = await coach.seal()
    assert snap is not None and snap.t_ms == 2_000
    assert snap.inputs["gaze_records"] == snap.inputs["voice_records"] == []
    assert snap.inputs["stt_status"] == "degraded"

@pytest.mark.anyio
async def test_late_context_restores_saved_slides_before_unsaved_changes():
    coach = CoachSession(TAKE_ID)
    coach.on_slide(SlideEvent(3, 0))
    coach.on_slide(SlideEvent(4, 2_000))
    tick(coach, 1_000, buffered=True)
    client = FakeClient()
    coach.enable(
        TakeContext(constants(), slides=(SlideEvent(1, 0), SlideEvent(2, 1_000))),
        client=client,
        store=FakeStore(),
    )

    assert coach._inputs_at(0, "ok")["slide"] == {"number": 1, "started_ms": 0}
    assert coach._inputs_at(1_000, "ok")["slide"] == {"number": 2, "started_ms": 1_000}
    tick(coach, 3_000, buffered=True)
    snap = await coach.seal()
    assert snap.inputs["slide"] == {"number": 4, "started_ms": 2_000}
    assert client.evaluated == []

@pytest.mark.anyio
async def test_live_slide_preserves_its_actual_start_time():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    coach.on_slide(SlideEvent(3, 59_000))
    tick(coach, 1_000)
    await settle()
    tick(coach, 60_000)
    await settle()
    assert client.evaluated[0]["inputs"]["slide"] is None
    assert client.evaluated[1]["inputs"]["slide"] == {"number": 3, "started_ms": 59_000}

@pytest.mark.anyio
async def test_a_new_connection_gets_the_display_again():
    """새로고침한 FE 는 상태 표시를 모른다. 떨어져 있는 동안 바뀐 표시도 다시 보낸다."""
    client, store = FakeClient(), FakeStore()
    client.replies = [
        reply(t_ms=1_000, indicators={"GAZE": "SCRIPT"}),
        reply(t_ms=2_000, indicators={"GAZE": "SCRIPT"}),
    ]
    coach = session(client, store)
    notified: list[int] = []

    async def notify(t_ms: int, r: EvaluateReply) -> None:
        notified.append(t_ms)

    coach.notify = notify
    tick(coach, 1_000)
    await settle()
    coach.forget_display()
    tick(coach, 2_000)
    await settle()

    assert notified == [1_000, 2_000]

@pytest.mark.anyio
async def test_old_or_repeated_metrics_do_not_call_the_coach():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)

    tick(coach, 2_000)
    await settle()
    tick(coach, 2_000)
    await settle()
    # 몰아 온 것이 더 늦은 시각을 먼저 올렸다. 그보다 이른 실시간 metrics 로는 부르지 않는다
    tick(coach, 5_000, buffered=True)
    tick(coach, 4_000)
    await settle()
    tick(coach, 6_000)
    await settle()

    assert [r["t_ms"] for r in client.evaluated] == [2_000, 6_000]

@pytest.mark.anyio
async def test_release_lets_go_of_the_connection_and_stops_saving():
    client, store = FakeClient(), FakeStore(errors=[db_down()])
    client.replies = [
        reply(t_ms=1_000, events=[{"kind": "INTERVENTION", "event_id": "iv-1000", "t_ms": 1_000}])
    ]
    coach = session(client, store)

    async def notify(t_ms: int, r: EvaluateReply) -> None:
        return None

    coach.notify = notify
    tick(coach, 1_000)
    await settle()
    await settle()

    coach.release()
    await asyncio.sleep(1.2)

    assert coach.notify is None
    # 실패한 저장을 다시 넣지 않는다 — finalize 저장이 같은 이벤트를 넣는다
    assert store.events == []
    assert coach.utterance_ends == []

@pytest.mark.anyio
async def test_seal_finishes_even_if_shutdown_cancels_the_running_evaluate():
    """앱을 내리며 진행 중인 판단을 끊어도, 그걸 기다리던 Take 끝내기는 이어 간다 (취소를 위로
    올리면 Take 를 끝난 것으로 적지 못한다)."""
    client, store = FakeClient(), FakeStore()
    client.release.clear()
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()

    sealing = asyncio.create_task(coach.seal())
    await settle()
    assert coach._inflight is not None
    coach._inflight.cancel()
    snapshot = await sealing

    assert snapshot is not None
    assert snapshot.t_ms == 1_000

@pytest.mark.anyio
async def test_released_session_ignores_what_comes_after():
    coach = session(FakeClient(), FakeStore())
    tick(coach, 1_000)
    coach.release()

    tick(coach, 2_000)
    coach.on_utterance_end(1_500)
    coach.on_slide(SlideEvent(2, 1_800))
    final_at = coach.on_final([("늦은", 1_600, 1_900)])

    assert coach._windows.gaze == {}
    assert coach._windows.words == []
    assert coach.utterance_ends == []
    assert coach._slide_changes == []
    assert final_at == 1_000

@pytest.mark.anyio
async def test_only_recent_utterance_ends_go_into_the_window():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    for ms in (120_000, 1_000, 61_000, 119_000, 1_000):
        coach.on_utterance_end(ms)

    tick(coach, 121_000)
    await settle()

    assert coach.utterance_ends == [1_000, 61_000, 119_000, 120_000]
    assert client.evaluated[0]["inputs"]["utterance_ends"] == [61_000, 119_000, 120_000]

@pytest.mark.anyio
async def test_a_stuck_screen_send_does_not_hold_back_the_next_evaluate(
    monkeypatch: pytest.MonkeyPatch,
):
    """보내기는 판단 하나 안에서 돈다. 소켓이 막혀 끝나지 않으면 다음 판단이 모두 건너뛰어진다."""
    monkeypatch.setattr(session_module, "NOTIFY_TIMEOUT_SEC", 0.05)
    client, store = FakeClient(), FakeStore()
    client.replies = [
        reply(t_ms=1_000, indicators={"GAZE": "SCRIPT"}),
        reply(t_ms=2_000, indicators={"GAZE": "SCRIPT"}),
    ]
    coach = session(client, store)
    tried: list[int] = []

    async def stuck(t_ms: int, r: EvaluateReply) -> None:
        tried.append(t_ms)
        await asyncio.Event().wait()

    coach.notify = stuck
    tick(coach, 1_000)
    await asyncio.sleep(0.1)
    tick(coach, 2_000)
    await asyncio.sleep(0.1)

    assert [r["t_ms"] for r in client.evaluated] == [1_000, 2_000]
    # 못 보낸 표시는 FE 가 모를 수 있어 같은 표시라도 다시 보낸다
    assert tried == [1_000, 2_000]

@pytest.mark.anyio
async def test_cancelling_a_seal_that_drops_a_late_evaluate_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
):
    """늦은 판단을 끊고 기다리는 동안 Take 끝내기 자체가 취소되면 그 취소를 위로 올린다."""
    monkeypatch.setattr(session_module, "SEAL_WAIT_SEC", 0.01)

    class SlowToStop(FakeClient):
        async def evaluate(self, request: dict[str, Any]) -> EvaluateReply | None:
            self.evaluated.append(request)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # 끊겨도 정리에 시간이 걸린다
                await asyncio.sleep(0.2)
                raise
            return None

    coach = session(SlowToStop(), FakeStore())
    tick(coach, 1_000)
    await settle()

    sealing = asyncio.create_task(coach.seal())
    await asyncio.sleep(0.05)
    sealing.cancel()

    with pytest.raises(asyncio.CancelledError):
        await sealing

@pytest.mark.anyio
async def test_release_lets_go_of_what_the_take_started_with():
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)

    coach.release()

    assert not coach.enabled
    assert (coach._constants, coach._client, coach._store) == (None, None, None)
    assert coach._event_buffer is None
    # 놓은 세션은 다시 켜지 않는다 (늦게 붙은 연결이 값을 읽어 와도)
    coach.enable(TakeContext(constants()), client=client, store=store)
    assert not coach.enabled

@pytest.mark.anyio
async def test_metrics_after_the_raw_buffers_close_stay_out_of_the_last_window():
    """Take 를 끝내며 1초 원자료 버퍼를 닫은 뒤 온 1초는 저장되지 않는다. 마지막 창 · 끝난 시각에
    넣으면 replay 와 달라진다."""
    client, store = FakeClient(), FakeStore()
    coach = session(client, store)
    tick(coach, 1_000)
    await settle()

    coach.seal_inputs()
    tick(coach, 2_000)
    coach.on_slide(SlideEvent(2, 1_500))
    snapshot = await coach.seal()

    assert snapshot is not None
    assert snapshot.t_ms == 1_000
    assert coach._slide_changes == []
