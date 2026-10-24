"""코치 finalize 실행부의 실패·복구 회귀 테스트."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import DataError

from pitch_coach_backend.coach import finalize
from pitch_coach_backend.coach.client import (
    ReplayRequired,
)
from pitch_coach_backend.coach.session import FinalizeSnapshot
from pitch_coach_backend.core.tasks import TaskRegistry
from tests.coach_fakes import (
    EVENT,
    LIVE_STATE,
    META,
    TAKE_ID,
    FakeClient,
    FakeStore,
    db_down,
    snapshot,
    source,
    take_result,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture(autouse=True)
def isolated_pending_finalizes(monkeypatch):
    monkeypatch.setattr(finalize, "_pending", {}, raising=False)

@pytest.mark.anyio
async def test_finalize_with_the_live_state_saves_the_result():
    client, store = FakeClient(), FakeStore(events=[EVENT])
    client_reply = take_result()
    client.finalize_replies = [client_reply]
    later = ({"kind": "OUTCOME", "event_id": "oc-1", "t_ms": 2}, META)

    await finalize.run(TAKE_ID, snapshot(events=[EVENT, later]), client=client, store=store)

    (request,) = client.finalized
    assert request["t_ms"] == 92_000
    assert request["coach_state"] == {"v": 2}
    assert "replay" not in request
    # 저장된 이벤트 뒤에 세션만 들고 있던 것을 붙인다. 같은 id 는 한 번
    assert [e["event_id"] for e in request["events"]] == ["iv-1", "oc-1"]
    (saved, events) = store.saved[0]
    assert saved is client_reply
    assert [e["event_id"] for e, _ in events] == ["iv-1", "oc-1"]
    assert store.failed == []

@pytest.mark.anyio
async def test_replay_required_sends_the_raw_data_once_more():
    client, store = FakeClient(), FakeStore(source=source())
    client.finalize_replies = [ReplayRequired(), take_result(replayed=True)]

    await finalize.run(TAKE_ID, snapshot(), client=client, store=store)

    first, second = client.finalized
    assert "replay" not in first
    replay = second["replay"]
    assert replay["slides"] == [{"number": 1, "started_ms": 0}]
    assert replay["words"] == [{"word": "가", "start_ms": 0, "end_ms": 500, "final_at_ms": 0}]
    assert replay["utterance_ends"] == [500]
    assert len(store.saved) == 1

@pytest.mark.anyio
async def test_without_a_live_state_finalize_starts_with_the_replay():
    """앱이 다시 떠 코치 세션을 잃었으면 처음부터 replay 를 붙인다 (2절 '복구')."""
    client, store = FakeClient(), FakeStore(source=source(events=[EVENT[0]]))
    client.finalize_replies = [take_result(replayed=True)]

    await finalize.run(TAKE_ID, None, client=client, store=store)

    (request,) = client.finalized
    assert request["coach_state"] is None
    assert request["t_ms"] == 92_000
    assert "replay" in request
    assert [e["event_id"] for e in request["events"]] == ["iv-1"]
    assert len(store.saved) == 1

@pytest.mark.anyio
async def test_a_disabled_coach_still_gets_a_replayed_result():
    client, store = FakeClient(), FakeStore(source=source())
    client.finalize_replies = [take_result(replayed=True)]
    snap = FinalizeSnapshot(
        t_ms=92_000, inputs={"stt_status": "ok"}, constants=None, coach_state=None
    )

    await finalize.run(TAKE_ID, snap, client=client, store=store)

    (request,) = client.finalized
    assert request["inputs"] == {"stt_status": "ok"}
    assert "replay" in request

@pytest.mark.anyio
async def test_no_raw_data_fails_the_take_without_calling_the_coach():
    client, store = FakeClient(), FakeStore(source=source(seconds=()))

    await finalize.run(TAKE_ID, None, client=client, store=store)

    assert client.finalized == []
    assert store.failed == [TAKE_ID]

@pytest.mark.anyio
async def test_finalize_that_fails_marks_the_take_failed():
    client, store = FakeClient(), FakeStore(source=source())
    client.finalize_replies = [None]

    await finalize.run(TAKE_ID, snapshot(), client=client, store=store)

    assert store.failed == [TAKE_ID]
    assert store.saved == []

@pytest.mark.anyio
async def test_finalize_of_a_take_no_longer_waiting_does_nothing():
    client, store = FakeClient(), FakeStore(source=None)

    await finalize.run(TAKE_ID, None, client=client, store=store)

    assert client.finalized == []
    assert store.failed == []

@pytest.mark.anyio
async def test_a_take_is_finalized_once_at_a_time_and_restart_resumes_waiting_takes():
    client, store = FakeClient(), FakeStore(source=source(), finalizing=[TAKE_ID])
    client.finalize_replies = [take_result(), take_result()]

    await finalize.resume(client=client, store=store)
    finalize.spawn(TAKE_ID, None, client=client, store=store)
    await asyncio.gather(*finalize._running.tasks())

    assert len(client.finalized) == 1
    assert len(finalize._running) == 0

@pytest.mark.anyio
async def test_db_outage_in_finalize_is_retried_then_left_for_the_restart(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """DB 장애 같은 오류는 잠깐 뒤 다시 한다. 끝내 안 되면 FINALIZING 으로 남겨 다시 뜰 때 이어
    간다."""
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", (0.0, 0.0))
    calls: list[uuid.UUID] = []

    class Broken(FakeStore):
        async def load_finalize_source(self, take_id):
            calls.append(take_id)
            raise db_down()

    store = Broken()
    with caplog.at_level("ERROR"):
        await finalize.run(TAKE_ID, None, client=FakeClient(), store=store)

    assert len(calls) == 3
    assert "나중에 이어 간다" in caplog.text
    assert store.failed == []

@pytest.mark.anyio
async def test_finalize_recovers_from_a_short_db_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", (0.0, 0.0))
    client = FakeClient()
    client.finalize_replies = [take_result(), take_result()]

    class Flaky(FakeStore):
        failures = 1

        async def save_take_result(self, take_id, reply, events) -> bool:
            if self.failures:
                self.failures -= 1
                raise db_down()
            return await super().save_take_result(take_id, reply, events)

    store = Flaky()

    await finalize.run(TAKE_ID, snapshot(), client=client, store=store)

    assert len(store.saved) == 1
    assert store.failed == []
    # 저장만 다시 한다. 받은 Take 결과로 — AI 를 다시 부르지 않는다
    assert len(client.finalized) == 1

@pytest.mark.anyio
@pytest.mark.parametrize("failed_step", ["load_events", "load_finalize_source", "save_take_result"])
async def test_finalize_resume_keeps_unsaved_events_after_db_retries_run_out(
    monkeypatch, failed_step
):
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", (0.0, 0.0))
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    client = FakeClient()
    client.finalize_replies = [take_result(), take_result()]
    store = FakeStore(source=source(), finalizing=[TAKE_ID])
    original = getattr(store, failed_step)

    async def unavailable(*args):
        raise db_down()

    monkeypatch.setattr(store, failed_step, unavailable)
    # 이벤트만 먼저 저장하는 경로도 막힌 장애다. 메모리 재료로 이어 가야 한다.
    monkeypatch.setattr(store, "append_events", unavailable)
    state = None if failed_step == "load_finalize_source" else LIVE_STATE
    await finalize.run(
        TAKE_ID, snapshot(coach_state=state, events=[EVENT]), client=client, store=store
    )
    assert store.failed == [] and store.saved == []

    monkeypatch.setattr(store, failed_step, original)
    await finalize.resume(client=client, store=store)
    await asyncio.gather(*finalize._running.tasks())

    assert [e["event_id"] for e in client.finalized[-1]["events"]] == ["iv-1"]
    assert [e["event_id"] for e, _ in store.saved[0][1]] == ["iv-1"]
    assert store.failed == []
    assert TAKE_ID not in finalize._pending

@pytest.mark.anyio
async def test_shutdown_saves_events_of_a_finalize_waiting_for_the_next_sweep(monkeypatch):
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", ())
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    store = FakeStore()
    append = store.append_events

    async def unavailable(*args):
        raise db_down()

    monkeypatch.setattr(store, "load_events", unavailable)
    monkeypatch.setattr(store, "append_events", unavailable)
    await finalize.run(TAKE_ID, snapshot(events=[EVENT]), client=FakeClient(), store=store)
    assert len(finalize._running) == 0
    assert TAKE_ID in finalize._pending and store.events == []

    monkeypatch.setattr(store, "append_events", append)
    await finalize.stop_all(timeout=1)
    assert [e["event_id"] for e, _ in store.events] == ["iv-1"]

@pytest.mark.anyio
@pytest.mark.parametrize("stale_before", [None, datetime.now(UTC)])
async def test_resume_releases_snapshots_of_takes_no_longer_finalizing(monkeypatch, stale_before):
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", ())
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    store = FakeStore()

    async def unavailable(*args):
        raise db_down()

    monkeypatch.setattr(store, "load_events", unavailable)
    monkeypatch.setattr(store, "append_events", unavailable)
    await finalize.run(TAKE_ID, snapshot(events=[EVENT]), client=FakeClient(), store=store)
    assert TAKE_ID in finalize._pending

    # DB 복구 뒤 삭제되었거나 다른 작업자가 완료해 FINALIZING 목록에서는 빠졌다.
    client = FakeClient()
    await finalize.resume(client=client, store=store, stale_before=stale_before)
    assert TAKE_ID not in finalize._pending
    assert client.finalized == []

@pytest.mark.anyio
async def test_stale_sweep_keeps_a_fresh_pending_snapshot(monkeypatch):
    monkeypatch.setattr(finalize, "_running", TaskRegistry())

    class Fresh(FakeStore):
        async def finalizing_takes(self, stale_before=None):
            self.stale_cutoffs.append(stale_before)
            return [TAKE_ID] if stale_before is None else []

    store, client = Fresh(), FakeClient()
    snap = snapshot(events=[EVENT])
    finalize._pending[TAKE_ID] = (snap, store)
    cutoff = datetime.now(UTC)
    await finalize.resume(client=client, store=store, stale_before=cutoff)

    assert finalize._pending[TAKE_ID][0] is snap
    assert store.stale_cutoffs == [cutoff, None]
    assert client.finalized == []

@pytest.mark.anyio
async def test_resume_does_not_release_a_running_finalize_snapshot(monkeypatch):
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    started, release = asyncio.Event(), asyncio.Event()

    class Blocking(FakeClient):
        async def finalize(self, request):
            started.set()
            await release.wait()
            return take_result()

    store, client = FakeStore(), Blocking()
    finalize.spawn(TAKE_ID, snapshot(events=[EVENT]), client=client, store=store)
    try:
        await asyncio.wait_for(started.wait(), 1)
        await finalize.resume(client=client, store=store)
        assert TAKE_ID in finalize._pending
    finally:
        release.set()
        await asyncio.gather(*finalize._running.tasks())
    assert TAKE_ID not in finalize._pending

@pytest.mark.anyio
async def test_error_that_retrying_cannot_fix_fails_the_take_at_once(
    monkeypatch: pytest.MonkeyPatch,
):
    """값이 칸에 맞지 않는 것 같은 실패는 다시 해도 같다. FINALIZING 으로 두면 다시 뜰 때마다 AI 를
    또 부르고 끝내 결과를 못 만든다 — 바로 FAILED 로 둔다."""
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", (0.0, 0.0))
    client = FakeClient()
    client.finalize_replies = [take_result()]

    class Overflow(FakeStore):
        tries = 0

        async def save_take_result(self, take_id, reply, events) -> bool:
            self.tries += 1
            raise DataError("INSERT", {}, ValueError("numeric field overflow"))

    store = Overflow()

    await finalize.run(TAKE_ID, snapshot(), client=client, store=store)

    assert store.tries == 1
    assert store.failed == [TAKE_ID]
    assert len(client.finalized) == 1

@pytest.mark.anyio
async def test_app_starts_even_if_the_waiting_takes_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
):
    """앱이 뜰 때 읽는 일은 덤이다. DB 가 늦어도 앱이 요청을 받기 시작해야 한다."""
    monkeypatch.setattr(finalize, "RESUME_READ_TIMEOUT_SEC", 0.05)

    class Hanging(FakeStore):
        async def finalizing_takes(self, stale_before=None):
            await asyncio.Event().wait()

    await asyncio.wait_for(finalize.resume(client=FakeClient(), store=Hanging()), 1.0)

@pytest.mark.anyio
async def test_takes_left_waiting_are_picked_up_without_a_restart(
    monkeypatch: pytest.MonkeyPatch,
):
    """DB 가 오래 안 돼 재시도를 다 썼거나 Take 결과를 띄우지 못한 Take 를 앱이 다시 뜰 때까지
    두지 않는다. 막 끝낸 Take 는 건드리지 않도록 오래된 것만 본다."""
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    client, store = FakeClient(), FakeStore(source=source(), finalizing=[TAKE_ID])
    client.finalize_replies = [take_result()]

    sweeping = asyncio.create_task(finalize.sweep_forever(client, store, interval_sec=0.01))
    for _ in range(100):
        if store.saved:
            break
        await asyncio.sleep(0.01)
    sweeping.cancel()

    assert len(store.saved) == 1
    cutoff = store.stale_cutoffs[0]
    assert cutoff is not None
    assert cutoff <= datetime.now(UTC) - finalize.STALE_FINALIZING

@pytest.mark.anyio
async def test_finalize_does_not_call_the_coach_for_a_take_no_longer_waiting():
    """seal 과 finalize 사이에 Take 가 지워지거나 이미 끝났다. AI 를 부르지 않는다."""

    class Gone(FakeStore):
        async def load_events(self, take_id):
            return None

    client = FakeClient()
    client.finalize_replies = [take_result()]
    store = Gone()

    await finalize.run(TAKE_ID, snapshot(), client=client, store=store)

    assert client.finalized == []
    assert store.saved == [] and store.failed == []

@pytest.mark.anyio
async def test_events_only_in_the_snapshot_are_kept_when_the_app_stops():
    """앱을 내리며 finalize 를 끊으면 다시 뜬 프로세스는 DB 의 이벤트만 읽는다. 발표 중 저장하지
    못해 재료에만 있는 코치 이벤트를 넣어 둔다 — 안 넣으면 그 개입 기록이 Take 결과에서 빠진다."""

    class Slow(FakeClient):
        async def finalize(self, request):
            self.finalized.append(request)
            await asyncio.Event().wait()

    client, store = Slow(), FakeStore()
    running = asyncio.create_task(
        finalize.run(TAKE_ID, snapshot(events=[EVENT]), client=client, store=store)
    )
    for _ in range(50):
        if client.finalized:
            break
        await asyncio.sleep(0.01)
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert [e["event_id"] for e, _ in store.events] == ["iv-1"]
