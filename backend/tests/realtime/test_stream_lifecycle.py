"""Take 스트림 수명 — 핸드오버 · cancel · 세션 회전 경합 · 쫓겨난 연결."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.connection import session
from pitch_coach_backend.realtime.dto import MetricsMessage
from pitch_coach_backend.realtime.stream import client as client_module
from pitch_coach_backend.realtime.stream import finish, registry, runtime, state
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttConfig,
)
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeSttSession,
    FakeTranscriptStore,
    FakeWebSocket,
    Plan,
    _wait_for,
    _wait_until_ok,
    closed_with,
    db_down,
    frame,
    handshake,
    metrics,
    row,
    stop,
    transcript,
    use,
    wait_state,
)

# ── 소유권·정리 (코드 리뷰 지적) ───────────────────────────────────────


def test_stop_timeout_cancels_the_stream_instead_of_leaking_it(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """drain 이 실패해도 레지스트리에서만 사라지고 태스크가 계속 도는 일이 없어야 한다."""
    monkeypatch.setattr(session, "STOP_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 5.0)
    adapter = use(FakeSttAdapter(Plan(swallow_close=True)))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        assert stop(ws)["state"] == "closed"
        assert closed_with(ws) == 1000

    assert registry.active_count() == 0
    assert adapter.session is not None
    # 태스크를 끊었으면 Deepgram 소켓도 닫혀야 한다 (shield 안의 abort)
    assert adapter.session.aborted


# ── ready 전송 실패 시 detach() 가 안 불리는 문제 ───────────────────────


@pytest.mark.anyio
async def test_ready_send_failure_still_detaches_from_the_stream(
    db_session: Session, user: User, token: str, monkeypatch: pytest.MonkeyPatch
):
    """ready 전송·resend_missed·pump_client 가 하나의 try/finally 로 묶여야 한다.
    그중 아무거나 먼저 실패해도 detach() 가 불려야 스트림에 grace 타이머가 걸린다
    (오디오를 한 번도 안 받았으면 바로 정리된다).
    안 그러면 죽은 self.ws 가 "현재 클라이언트" 로 영원히 남아 Deepgram 세션이
    다시는 정리되지 않는다. 그리고 run() 은 이 예외를 밖으로 내보내지 않는다 — 클라이언트가
    사라진 건 오류가 아니라 흔한 종료라, 새면 uvicorn 이 연결마다 트레이스를 남긴다."""
    adapter = FakeSttAdapter()
    ws = FakeWebSocket(
        inbound=[
            {"type": "websocket.receive", "text": json.dumps({"type": "auth", "token": token})}
        ],
        fail_send_after=1,  # 첫 send_text = ready 메시지에서 터진다
    )
    realtime = session.RealtimeSession(
        ws,
        take_id=TAKE_ID,
        db=db_session,
        stt_adapter=adapter,
        transcript_store=FakeTranscriptStore(),
    )
    # 붙인 스트림을 잡아 둔다. 오디오 없이 끊기면 바로 정리돼 레지스트리에서 빠질 수 있다
    attached: list[TakeStream] = []
    attach = registry.attach

    async def spy(*args, **kwargs) -> TakeStream:
        stream = await attach(*args, **kwargs)
        attached.append(stream)
        return stream

    monkeypatch.setattr(registry, "attach", spy)

    await realtime.run()  # 예외가 새지 않는다

    stream = attached[0]
    assert not stream.has_client, "detach() 가 안 불리면 죽은 ws 가 현재 클라이언트로 남는다"
    stream.cancel()


# ── 종료 중 동시 재연결이 스트림을 두 번 만드는 문제 ────────────────────


@pytest.mark.anyio
async def test_concurrent_attach_during_handover_does_not_duplicate_stream(
    monkeypatch: pytest.MonkeyPatch,
):
    """확인(get) -> 생성(TakeStream) 이 원자적이지 않으면, 정리 중인 스트림에
    두 연결이 동시에 오는 순간 둘 다 "없다" 를 보고 각자 새 스트림·Deepgram 세션을
    만든다. 하나는 레지스트리에서 밀려나 고아로 남는다."""
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan(), Plan())

    first = await registry.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    await _wait_until_ok(first)
    first.request_stop()  # swallow_close 라서 스스로는 절대 안 끝난다 -> is_stopping 만 유지된다

    second, third = await asyncio.gather(
        registry.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        ),
        registry.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        ),
    )

    assert second is third, "같은 take_id 로 겹친 재연결은 같은 스트림으로 수렴해야 한다"
    assert registry.active_count() == 1
    # Deepgram 연결은 처음(1) + 핸드오버 뒤 새로 한 번(1) = 2 여야 한다. 락이 없으면 3 이 된다
    assert len(adapter.sessions) == 2


# ── cancel() 이 요청을 완료로 착각하는 문제 ─────────────────────────────


@pytest.mark.anyio
async def test_cancel_waits_for_real_deepgram_cleanup_before_reporting_closed():
    """cancel() 은 취소 '요청' 일 뿐이다. task.cancel() 은 다음 await 지점에서
    CancelledError 를 던지도록 예약할 뿐 그 자리에서 정리가 끝나는 게 아니다.
    실제 정리(Deepgram 소켓을 shield 안에서 닫는 것)가 끝나야 closed 여야 한다."""
    adapter = FakeSttAdapter(Plan(abort_delay=0.15))
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()
    assert not stream.is_finished, "cancel() 직후인데 벌써 닫혔다고 보고했다"
    await asyncio.sleep(0.05)  # abort_delay(0.15) 보다 짧다 — 아직 안 끝났어야 한다
    assert not stream.is_finished, "cancel() 이 실제 정리(abort)를 기다리지 않았다"

    closed = await stream.wait_closed(timeout=1.0)
    assert closed is True
    assert adapter.sessions[0].aborted is True


# ── 긴 갭 직후 stop 이 남은 오디오를 버리는 문제 ────────────────────────


@pytest.mark.anyio
async def test_stop_immediately_after_long_gap_still_flushes_carried_audio():
    """세션 회전(_ROTATE)과 stop 이 겹치면, 회전 직전에 넘겨받은 캐리 프레임과 그 뒤
    큐에 남은 stop 신호가 새 세션을 하나 더 열어서라도 처리돼야 한다."""
    adapter = FakeSttAdapter(Plan(), Plan())
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.push(frame(1, 0))
    # push()·request_stop() 은 둘 다 동기(non-async) 라 이 세 줄 사이에는 백그라운드
    # 태스크가 끼어들 틈이 없다 — "회전을 부르는 프레임과 stop 이 동시에 온다" 는
    # 가장 흔한 경합 모양을 그대로 재현한다
    stream.push(frame(2, 10_100))  # 10초 갭 -> 캐리로 넘어가며 세션 회전을 요구한다
    stream.request_stop()

    closed = await stream.wait_closed(timeout=2.0)
    assert closed is True
    # 회전이 필요했으니 Deepgram 세션이 두 번 열려야 한다. stop 을 먼저 보고 끝내면 1 뿐이다
    assert len(adapter.sessions) == 2
    # 회전으로 넘어간 캐리 프레임(3,200 bytes)이 새 세션에 실제로 전달됐다
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]


# ── 핸드오버 타임아웃 시 옛 스트림이 정리 안 되는 문제 ──────────────────


@pytest.mark.anyio
async def test_handover_timeout_cancels_the_old_stream_instead_of_orphaning_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """옛 스트림의 정리가 정상 종료 상한(DRAIN_TIMEOUT_SEC)을 넘기면 핸드오버는 기다리기를
    포기한다. 그때 그냥 forget() 만 하면 옛 스트림은 레지스트리에서만 사라질 뿐 자기 페이스대로
    계속 돈다 — Deepgram 연결이 안 끊긴다. cancel() 로 강제로 끊어야 한다."""
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    # swallow_close: CloseStream 을 보내도 스스로는 절대 안 끝난다 (느린 정상 drain 재현)
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan())

    old_stream = await registry.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    await _wait_until_ok(old_stream)
    old_stream.request_stop()  # 정상적으로 정리를 시작했다 (아직 안 끝났을 뿐)

    new_stream = await registry.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )

    assert new_stream is not old_stream
    # 옛 스트림이 실제로 끊겼는지 — cancel() 이 안 불리면 이 대기가 계속 False 로 남는다
    assert await old_stream.wait_closed(timeout=1.0) is True, (
        "핸드오버 타임아웃 뒤에도 옛 스트림이 스스로 안 끝나면 강제로 끊어야 한다"
    )
    assert adapter.sessions[0].aborted is True
    await _wait_until_ok(new_stream)
    await asyncio.sleep(0.01)  # 세션 태스크가 뜨기 전에 끊으면 "never awaited" 경고가 난다
    new_stream.cancel()
    await new_stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_handover_numbers_after_the_final_the_old_stream_is_still_saving(
    monkeypatch: pytest.MonkeyPatch,
):
    """정리 중인 스트림이 final 을 저장하는 도중에 끊겨도 그 저장은 Take 의 버퍼가 끝까지 한다.
    새 스트림은 저장된 번호와 아직 못 넣은 번호 중 큰 쪽 뒤에서 잇는다 — 저장된 번호만 보면 같은
    seq 로 저장하다 UNIQUE 위반으로 버려진다."""
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    adapter = FakeSttAdapter(
        Plan(replies=[transcript(0, 200, "마지막 말", is_final=True)], swallow_close=True),
        Plan(),
    )
    store = FakeTranscriptStore(append_delay=0.3)

    old_stream = await registry.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
    )
    await _wait_until_ok(old_stream)
    old_stream.push(frame(1, 0))
    await _wait_for(lambda: adapter.sessions[0].audio)
    old_stream.request_stop()  # final 저장(0.3s) 중에 drain 상한(0.05s)이 지나 끊긴다

    new_stream = await registry.attach(
        take_id,
        FakeWebSocket(),
        owner_id=owner_id,
        stt_adapter=adapter,
        config=SttConfig(),
        store=store,
    )

    assert new_stream._transcripts.segment_no == 2, "옛 스트림이 저장 중인 seq 1 을 다시 쓴다"
    assert await new_stream._record.finals.flush()
    assert [(seg.seq, seg.transcript) for seg in store.segments] == [(1, "마지막 말")]
    await _wait_until_ok(new_stream)
    await asyncio.sleep(0.01)  # 세션 태스크가 뜨기 전에 끊으면 "never awaited" 경고가 난다
    new_stream.cancel()
    await new_stream.wait_closed(timeout=1.0)


# ── 태스크가 한 번도 실행되기 전에 취소되면 종료가 기록 안 되는 문제 ────


@pytest.mark.anyio
async def test_cancel_before_task_ever_runs_still_reports_closed():
    """asyncio.create_task() 직후 바로 cancel() 하면(태스크가 단 한 번도 스텝되지
    않은 채로), 코루틴 본문이 아예 실행되지 않아 _run() 의 finally 조차 안 돈다.
    그러면 _finish() 를 아무도 안 불러서 wait_closed() 가 영원히 False 만 준다."""
    adapter = FakeSttAdapter(Plan())
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    stream.cancel()  # start() 직후, 태스크가 단 한 번도 스텝되지 않은 채로 취소한다

    closed = await stream.wait_closed(timeout=1.0)
    assert closed is True
    assert stream.is_finished is True
    assert registry.active_count() == 0


# ── 코드 리뷰 반영: cancel 정리 상한 · reaper 참조 · take 별 락 ─────────


@pytest.mark.anyio
async def test_cancel_keeps_a_reference_to_the_reaper_task():
    """asyncio 는 태스크를 약하게만 잡는다. 참조 없이 create_task 만 하면 정리가 끝나기 전에
    GC 될 수 있다 (asyncio.create_task 문서의 경고)."""
    adapter = FakeSttAdapter(Plan(abort_delay=0.05))
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()

    assert isinstance(stream._reaper, asyncio.Task)
    assert await stream.wait_closed(timeout=1.0) is True
    await asyncio.wait_for(stream._reaper, 1.0)  # 참조가 있으니 끝까지 기다릴 수 있다


@pytest.mark.anyio
async def test_abort_is_bounded_so_cancel_cannot_hang_on_deepgram(
    monkeypatch: pytest.MonkeyPatch,
):
    """Deepgram 소켓 닫기는 shield 안에서 돌아 바깥에서 끊을 수 없다. 상대가 close 에 답하지
    않으면 상한을 두지 않는 한 영영 기다린다."""
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 0.1)
    adapter = FakeSttAdapter(Plan(abort_delay=10.0))  # 사실상 답이 없다
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()

    assert await stream.wait_closed(timeout=1.0) is True
    assert adapter.sessions[0].aborted is False, "답이 없어서 버리고 나왔다"


@pytest.mark.anyio
async def test_wait_cancelled_gives_up_and_reports_closed(monkeypatch: pytest.MonkeyPatch):
    """CANCEL_TIMEOUT_SEC 안에 정리가 안 끝나면 (코드 리뷰 케이스 1) 닫힌 것으로 확정한다.
    FE 는 더 붙잡히지 않고, 뒤늦게 끝난 정리는 idempotent 한 _finish 로 흡수된다."""
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 1.0)
    adapter = FakeSttAdapter(Plan(abort_delay=0.3))
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.cancel()
    assert await stream.wait_cancelled(0.05) is False
    assert stream.is_finished and stream.state == "closed"

    await asyncio.sleep(0.4)  # abort 가 뒤늦게 끝난다
    assert adapter.sessions[0].aborted is True
    assert stream.is_finished


@pytest.mark.anyio
async def test_handover_wait_does_not_block_other_users(monkeypatch: pytest.MonkeyPatch):
    """한 사용자의 핸드오버 대기(DRAIN_TIMEOUT_SEC)가 다른 사용자의 연습을 막으면 안 된다.
    같은 사용자의 다른 Take는 사용자당 연습 하나 정책으로 제한한다."""
    monkeypatch.setattr(stt_loop, "DRAIN_TIMEOUT_SEC", 0.5)
    adapter = FakeSttAdapter(Plan(swallow_close=True), Plan(), Plan())
    take_a, take_b = uuid.uuid7(), uuid.uuid7()
    owners = {take_a: uuid.uuid7(), take_b: uuid.uuid7()}

    def attach(take_id: uuid.UUID):
        return registry.attach(
            take_id,
            FakeWebSocket(),
            owner_id=owners[take_id],
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        )

    old_a = await attach(take_a)
    await _wait_until_ok(old_a)
    old_a.request_stop()  # swallow_close 라 스스로 안 끝난다 -> 재연결은 핸드오버를 기다린다

    async def attach_b_timed() -> tuple[float, TakeStream]:
        started = time.monotonic()
        stream = await attach(take_b)
        return time.monotonic() - started, stream

    new_a, (elapsed_b, stream_b) = await asyncio.gather(attach(take_a), attach_b_timed())

    assert elapsed_b < 0.25, f"다른 사용자가 핸드오버 대기에 같이 막혔다 ({elapsed_b:.2f}s)"
    assert new_a is not old_a and stream_b is not old_a
    assert registry.active_count() == 2
    assert not state.attach_locks, "다 쓴 락은 남기지 않는다"


@pytest.mark.anyio
async def test_frames_from_a_taken_over_connection_are_ignored():
    """다른 탭이 이어받은 뒤 쫓겨난 연결이 닫히기 전에 보낸 프레임은 버린다.
    섞이면 새 탭의 seq 를 앞질러 새 탭 오디오가 역행으로 버려진다."""
    adapter = FakeSttAdapter()
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    stream.push(frame(1, 0), source=old_tab)
    stream.push(frame(2, 100), source=old_tab)

    await stream.attach(new_tab)
    stream.push(frame(3, 200), source=old_tab)  # 쫓겨난 탭이 마지막으로 보낸 것
    stream.push(frame(1, 0), source=new_tab)
    stream.push(frame(2, 100), source=new_tab)

    assert stream.sequencer.frames == 4
    assert stream.sequencer.dropped == 0
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_stop_from_a_taken_over_connection_is_ignored():
    """쫓겨난 연결이 닫히기 전에 보낸 stop 도 버린다. 받으면 스트림이 멈추고, 이어받은 탭의
    오디오는 멈춘 스트림에 아무 알림 없이 버려진다."""
    adapter = FakeSttAdapter()
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab = FakeWebSocket(
        inbound=[{"type": "websocket.receive", "text": json.dumps({"type": "stop"})}]
    )
    new_tab = FakeWebSocket()
    await stream.attach(old_tab)
    await stream.attach(new_tab)

    realtime = session.RealtimeSession(
        old_tab,
        take_id=stream.take_id,
        db=None,  # type: ignore[arg-type] - 펌프는 DB 를 안 본다
        stt_adapter=adapter,
        transcript_store=FakeTranscriptStore(),
    )
    await realtime._pump_client(stream)

    assert not stream.is_stopping
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


class StuckWebSocket(FakeWebSocket):
    """받지 않는 상대. `send_stuck` · `close_stuck` 인 단계는 끝나지 않는다."""

    def __init__(self, *, send_stuck: bool = False, close_stuck: bool = False) -> None:
        super().__init__()
        self.send_stuck = send_stuck
        self.close_stuck = close_stuck
        self.close_tried = False

    async def send_text(self, text: str) -> None:
        if self.send_stuck:
            await asyncio.Event().wait()
        await super().send_text(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_tried = True
        if self.close_stuck:
            await asyncio.Event().wait()
        await super().close(code, reason)


def _taken_over(ws: FakeWebSocket) -> bool:
    return [json.loads(t)["code"] for t in ws.sent] == ["TAKE_TAKEN_OVER"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("send_stuck", "close_stuck"), [(True, False), (False, True), (True, True)]
)
async def test_takeover_does_not_wait_forever_for_the_old_connection(
    monkeypatch: pytest.MonkeyPatch, send_stuck: bool, close_stuck: bool
):
    """이어받을 때 옛 연결에 알리고 닫기를 기다린다. 옛 연결이 받지 않으면 새 연결의 ready 가 그
    연결에 붙잡힌다. 알림이 막혀도 닫기는 해 본다."""
    monkeypatch.setattr(client_module, "CLOSE_STEP_TIMEOUT_SEC", 0.05)
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab = StuckWebSocket(send_stuck=send_stuck, close_stuck=close_stuck)
    new_tab = FakeWebSocket()
    await stream.attach(old_tab)

    started = time.monotonic()
    await asyncio.wait_for(stream.attach(new_tab), timeout=1.0)

    assert time.monotonic() - started < 0.5
    assert stream.is_client(new_tab)
    assert old_tab.close_tried
    assert _taken_over(old_tab) is not send_stuck
    # 옛 연결이 뒤늦게 끊겨도 새 연결은 그대로 붙어 있다
    stream.detach(old_tab)
    assert stream.is_client(new_tab)
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_metrics_from_a_connection_taken_over_while_closing_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(client_module, "CLOSE_STEP_TIMEOUT_SEC", 0.05)
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    old_tab = StuckWebSocket(send_stuck=True, close_stuck=True)
    await stream.attach(old_tab)
    await asyncio.wait_for(stream.attach(FakeWebSocket()), timeout=1.0)

    stream.push_metrics(MetricsMessage.model_validate(metrics(1_000)), source=old_tab)
    stream.push(frame(1, 0), source=old_tab)

    assert not stream._record.metrics.pending
    assert stream.sequencer.frames == 0
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_concurrent_takeovers_of_a_stuck_connection_share_one_stream(
    monkeypatch: pytest.MonkeyPatch,
):
    """옛 연결이 받지 않는 동안 탭 둘이 같이 이어받는다. 둘 다 상한 안에 끝나고 스트림 · Deepgram
    세션은 하나다. 나중에 붙은 탭이 남는다."""
    monkeypatch.setattr(client_module, "CLOSE_STEP_TIMEOUT_SEC", 0.05)
    take_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    adapter = FakeSttAdapter()

    def attach(ws: FakeWebSocket):
        return registry.attach(
            take_id,
            ws,
            owner_id=owner_id,
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        )

    old_tab = StuckWebSocket(send_stuck=True, close_stuck=True)
    first = await attach(old_tab)
    await _wait_until_ok(first)
    second_tab, third_tab = FakeWebSocket(), FakeWebSocket()

    second, third = await asyncio.wait_for(
        asyncio.gather(attach(second_tab), attach(third_tab)), timeout=1.0
    )

    assert first is second is third
    assert registry.active_count() == 1
    assert len(adapter.sessions) == 1
    assert first.is_client(third_tab)
    assert _taken_over(second_tab)


@pytest.mark.anyio
async def test_sign_out_does_not_wait_forever_for_a_stuck_connection(
    monkeypatch: pytest.MonkeyPatch,
):
    """로그아웃은 그 사용자의 연결을 모두 닫고 나서 답한다. 받지 않는 연결 하나가 응답을 붙잡지
    않는다."""
    monkeypatch.setattr(client_module, "CLOSE_STEP_TIMEOUT_SEC", 0.05)
    owner_id = uuid.uuid7()
    stream = await registry.attach(
        uuid.uuid7(),
        StuckWebSocket(send_stuck=True, close_stuck=True),
        owner_id=owner_id,
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )

    await asyncio.wait_for(registry.sign_out(owner_id), timeout=1.0)

    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_stream_without_audio_stops_on_detach_instead_of_waiting_grace():
    """오디오를 한 번도 안 받은 스트림은 끊기면 바로 정리한다. 살려 둘 전사가 없는데 grace 동안
    Deepgram 세션만 붙든다 — 종료 중에 새로고침한 FE 가 stop 만 보내러 붙었다 끊은 경우."""
    adapter = FakeSttAdapter()

    def make() -> TakeStream:
        return registry.create_stream(
            uuid.uuid7(),
            owner_id=uuid.uuid7(),
            stt_adapter=adapter,
            config=SttConfig(),
            store=FakeTranscriptStore(),
        )

    silent, spoken = make(), make()
    silent_tab, spoken_tab = FakeWebSocket(), FakeWebSocket()
    await silent.attach(silent_tab)
    await spoken.attach(spoken_tab)
    spoken.push(frame(1, 0), source=spoken_tab)

    silent.detach(silent_tab)
    spoken.detach(spoken_tab)

    assert silent.is_stopping
    assert not spoken.is_stopping, "오디오를 받은 스트림은 grace 동안 재연결을 기다린다"
    for stream in (silent, spoken):
        stream.cancel()
        await stream.wait_closed(timeout=1.0)


def _slow_after_first(adapter: FakeSttAdapter, delay: float) -> None:
    """첫 세션 뒤로는 Deepgram 연결이 delay 초 걸린다 (세션을 가는 동안 오디오가 큐에 쌓인다)."""
    connect = adapter.connect

    async def slow(config: SttConfig) -> FakeSttSession:
        if adapter.sessions:
            await asyncio.sleep(delay)
        return await connect(config)

    adapter.connect = slow  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_audio_dropped_while_rotating_is_filled_with_silence(
    monkeypatch: pytest.MonkeyPatch,
):
    """세션을 가는 동안 큐가 넘쳐 드롭된 것은 넘겨받은 프레임 **뒤**의 오디오다. 새 세션의
    base 가 흡수하면 안 된다 — 그만큼 이후 전사 시각이 앞당겨진다. 새로고침할 때마다 세션을 간다."""
    monkeypatch.setattr(runtime, "QUEUE_MAX_FRAMES", 10)
    adapter = FakeSttAdapter()
    _slow_after_first(adapter, 0.3)
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    for i in range(30):
        stream.push(frame(i + 1, i * 100), source=old_tab)
        await asyncio.sleep(0)
    await _wait_for(lambda: adapter.sessions[0].audio_ms == 3000)

    # 새로고침. seq 는 1 부터, offset 은 이어받은 무대 시계부터
    await stream.attach(new_tab)
    for i in range(5):
        stream.push(frame(i + 1, 3000 + i * 100), source=new_tab)
    await _wait_for(lambda: "CloseStream" in adapter.sessions[0].controls)
    # 새 세션이 붙기를 기다리는 동안 들어온 실시간 프레임. 큐(10)를 넘는다
    for i in range(5, 30):
        stream.push(frame(i + 1, 3000 + i * 100), source=new_tab)
    await _wait_for(lambda: len(adapter.sessions) == 2 and adapter.sessions[1].audio_ms > 0)
    await asyncio.sleep(0.05)

    assert stream.dropped_audio_ms > 0
    # 마지막 프레임 끝은 Take 기준 6000ms. take_ms = base + deepgram_ms 가 성립해야 한다
    assert stream._stt.base_offset_ms == 3000
    assert stream._stt.base_offset_ms + adapter.sessions[1].audio_ms == 6000
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


@pytest.mark.anyio
async def test_dropped_restart_frame_still_rotates(monkeypatch: pytest.MonkeyPatch):
    """새로고침 뒤 첫 프레임(restart)이 큐가 넘쳐 드롭돼도 세션을 간다. 표시가 프레임과 함께
    사라지면 새 탭 오디오가 옛 세션에 이어 붙는다."""
    monkeypatch.setattr(runtime, "QUEUE_MAX_FRAMES", 5)
    adapter = FakeSttAdapter()
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    old_tab, new_tab = FakeWebSocket(), FakeWebSocket()
    await stream.attach(old_tab)
    for i in range(10):
        stream.push(frame(i + 1, i * 100), source=old_tab)
        await asyncio.sleep(0)
    await _wait_for(lambda: adapter.sessions[0].audio_ms == 1000)

    # 펌프가 돌기 전에 몰려 들어와 restart 프레임(seq 1)부터 세 개가 드롭된다
    await stream.attach(new_tab)
    for i in range(8):
        stream.push(frame(i + 1, 1000 + i * 100), source=new_tab)
    await _wait_for(lambda: len(adapter.sessions) == 2 and adapter.sessions[1].audio_ms == 500)

    assert adapter.sessions[0].audio_ms == 1000, "옛 세션에 이어 붙지 않는다"
    # 살아남은 첫 프레임(seq 4, 1300ms)이 새 세션의 base 다
    assert stream._stt.base_offset_ms == 1300
    stream.cancel()
    await stream.wait_closed(timeout=1.0)


# ── 그 밖의 예외 케이스 ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_audio_after_stop_is_ignored():
    """stop 뒤의 오디오는 아무도 소비하지 않는다. 큐에 넣으면 통계(frames)만 어긋난다."""
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    stream.push(frame(1, 0))
    stream.request_stop()
    stream.push(frame(2, 100))

    assert await stream.wait_closed(timeout=2.0)
    assert stream.sequencer.frames == 1


@pytest.mark.anyio
async def test_audio_that_never_reached_deepgram_is_reported_as_lost():
    """Deepgram 이 끝내 안 붙은 채 끝나면 큐에 남은 오디오는 유실이다. 마지막 stt_status 의
    lost_ms 가 이걸 빼먹으면 리포트가 "STT 다 받았다" 고 잘못 말한다."""
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=FakeSttAdapter(fail_times=1_000),
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    for i in range(3):
        stream.push(frame(i + 1, i * 100))
    await asyncio.sleep(0.05)

    stream.cancel()
    assert await stream.wait_closed(timeout=1.0)
    assert stream.dropped_audio_ms == 300


@pytest.mark.anyio
async def test_unexpected_exception_in_session_reconnects_instead_of_dying(
    caplog: pytest.LogCaptureFixture,
):
    """어댑터 버그 같은 예상 못 한 예외로 태스크가 죽으면 FE 는 아무 알림 없이 STT 만 조용히
    멈춘 상태가 된다. Deepgram 이 끊긴 것과 같게 다뤄 재접속해야 한다."""
    adapter = FakeSttAdapter(Plan(explode_on_send=True), Plan())
    stream = registry.create_stream(
        uuid.uuid7(),
        owner_id=uuid.uuid7(),
        stt_adapter=adapter,
        config=SttConfig(),
        store=FakeTranscriptStore(),
    )
    stream.start()
    await _wait_until_ok(stream)

    with caplog.at_level("ERROR"):
        stream.push(frame(1, 0))  # 첫 세션이 터진다
        deadline = time.monotonic() + 2.0
        while (len(adapter.sessions) < 2 or stream.state != "ok") and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    assert len(adapter.sessions) == 2 and stream.state == "ok"
    assert "예상 못 한 예외" in caplog.text
    stream.push(frame(2, 100))
    stream.request_stop()
    assert await stream.wait_closed(timeout=2.0)
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]


# ── 앱을 내릴 때 ──────────────────────────────────────────────────────


def _segment(seq: int) -> TranscriptSegmentCreateDTO:
    return TranscriptSegmentCreateDTO(
        seq=seq,
        stt_session_no=1,
        start_ms=seq * 1000,
        end_ms=seq * 1000 + 500,
        transcript="하나",
        words=[],
        confidence=0.9,
        speech_final=True,
    )


@pytest.mark.anyio
async def test_shutdown_closes_slow_streams_together_then_saves_records(
    monkeypatch: pytest.MonkeyPatch,
):
    """컨테이너는 정해 둔 시간이 지나면 프로세스를 강제로 끝낸다. CloseStream 에 답하지 않는
    스트림을 하나씩 기다리면 그 시간이 스트림 수만큼 늘어 기록을 저장하기 전에 잘린다. 스트림은
    같이 닫고, DB 가 느려도 남은 1초 원자료와 확정 전사를 같이 저장해 상한 안에 끝낸다."""
    monkeypatch.setattr(registry, "SHUTDOWN_CLOSE_WAIT_SEC", 0.2)
    monkeypatch.setattr(stt_loop, "ABORT_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(finish, "FLUSH_METRICS_TIMEOUT_SEC", 0.4)
    # 확정 전사는 받는 대로 넣는데 처음 넣기가 실패해 들고 있다. 앱을 내릴 때 다시 넣는다
    store = FakeTranscriptStore(append_delay=0.35, errors=[db_down()] * 3)
    streams = []
    for i in range(1, 4):
        stream = registry.create_stream(
            uuid.uuid7(),
            owner_id=uuid.uuid7(),
            stt_adapter=FakeSttAdapter(Plan(swallow_close=True)),
            config=SttConfig(),
            store=store,
        )
        stream.start()
        await _wait_until_ok(stream)
        state.streams[stream.take_id] = stream
        record = state.takes[stream.take_id]
        record.metrics.add(row(i * 1000))
        record.finals.add(_segment(i))
        streams.append(stream)
    await _wait_for(lambda: not store.errors)
    await asyncio.sleep(0.05)

    started = time.monotonic()
    await registry.stop_all()
    elapsed = time.monotonic() - started

    # 하나씩 닫으면 3 × 0.2 + 0.35 초, 원자료 → 확정 전사를 차례로 저장하면 0.2 + 2 × 0.35 초다
    assert elapsed < (
        registry.SHUTDOWN_CLOSE_WAIT_SEC
        + stt_loop.ABORT_TIMEOUT_SEC
        + finish.FLUSH_METRICS_TIMEOUT_SEC
    )
    assert sorted(store.metrics) == [1000, 2000, 3000]
    assert sorted(store.take_ids) == sorted(stream.take_id for stream in streams)
