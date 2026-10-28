"""Take 별 스트림 · 기록의 레지스트리. 프로세스에 하나이고 요청 · 연결 · 재연결이 같이 쓴다.

연결(`connection/session.py`)은 연결마다 새로 만들지만, 그 연결은 여기서 같은 Take 의 스트림을
찾는다 (`attach`). 스트림 맵 · attach 잠금 · 스트림이 바뀌어도 이어질 Take 별 기록(말 없음 · 서버
시계 · 1초 원자료 · 코치, `record.TakeRecord`) · 이 프로세스에서 끝낸 Take 는 `state.py` 에 있다.
스트림은 이 상태를 직접 만지지 않고 `record.StreamHost` 로 알린다.

떠난 Take 를 끝내는 일(`_end_abandoned`)은 여기서 한다. 떠난 Take 타이머 자체는 `abandon.py` 고,
사용자 · 자동 종료로 Take 를 끝내는 일(마지막 창 · 원자료 저장 · DB 에 적기 · Take 결과 띄우기)은
`finish.py` 다.

앱 수명: `main.lifespan` 이 뜰 때 `arm_abandoned_takes` 로 떠난 Take 타이머를 다시 걸고, 내릴 때
`stop_all` 로 스트림 drain · 기록 flush · 백그라운드 태스크 정리를 기다린다.

uvicorn 워커 하나 · 프로세스 하나가 전제다 (`state.py`).
"""

import asyncio
import functools
import logging
import uuid
from collections.abc import Coroutine, Iterable
from datetime import UTC, datetime

from fastapi import WebSocket

from pitch_coach_backend.coach.requests import TakeContext
from pitch_coach_backend.coach.session import (
    SEAL_WAIT_SEC,
)
from pitch_coach_backend.realtime.dto import (
    AutoEndReason,
)
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream import abandon, finish, runtime, state
from pitch_coach_backend.realtime.stream.abandon import LeftReason
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy
from pitch_coach_backend.realtime.stream.record import CoachDeps, TakeRecord
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttAdapter,
    SttConfig,
)

logger = logging.getLogger(__name__)

# 사용자는 한 번에 연습 하나만 한다. 같은 Take의 재연결은 기존 스트림을 이어받는다.
# 다른 Take가 붙어 있으면 거절하고, 끊겨 grace를 기다리던 스트림은 닫은 뒤 교체한다.
MAX_STREAMS_PER_USER = 1


class StreamOwnerMismatch(Exception):
    """다른 사용자가 이미 쓰고 있는 Take 다."""


class TooManyStreams(Exception):
    """이 사용자가 다른 Take 로 연 스트림이 상한(`MAX_STREAMS_PER_USER`)에 닿았다."""


class TakeEnded(Exception):
    """이 프로세스에서 끝낸 Take 다. DB 에 적히기 전이라도 다시 붙지 못한다.

    BE 가 끝냈으면 `reason` 에 이유가 있다 (자동 종료 · 떠남). 사용자가 끝냈으면 None.
    """

    def __init__(self, reason: AutoEndReason | LeftReason | None = None) -> None:
        super().__init__(reason)
        self.reason = reason


class _Host:
    """스트림이 알리는 자리 (`record.StreamHost`). `state` 의 맵 · 떠난 Take 타이머 · 태스크로 이어
    준다."""

    def stream_started(self, take_id: uuid.UUID) -> None:
        abandon.cancel(take_id)

    def stream_closed(
        self, stream: TakeStream, store: TranscriptStore, *, discarded: bool, tab_closed: bool
    ) -> None:
        forget(stream)
        if discarded or stream.take_id in state.ended:
            return
        # Take 는 끝나지 않고 스트림만 닫혔다 (grace 만료 등). 아무도 다시 붙지 않으면 끝낸다
        if tab_closed:
            # 창을 닫았다. 새로고침이면 그 안에 다시 붙는다
            arm_abandon(
                stream.take_id,
                store,
                closed_at=datetime.now(UTC),
                delay_sec=abandon.CLOSED_TAB_WAIT_SEC,
                end_reason="CLOSED",
            )
            return
        arm_abandon(stream.take_id, store, closed_at=datetime.now(UTC))

    def take_ended(self, take_id: uuid.UUID, reason: AutoEndReason | None) -> None:
        if reason is not None:
            state.ended[take_id] = reason
        elif take_id not in state.ended:
            state.ended[take_id] = None

    def spawn(self, coro: Coroutine[object, object, None], *, name: str) -> None:
        state.spawn(coro, name=name)

    async def finish_take(
        self,
        take_id: uuid.UUID,
        store: TranscriptStore,
        *,
        end_reason: str | None,
        coach: CoachDeps | None,
    ) -> bool:
        return await finish.finish_take(take_id, store, end_reason=end_reason, coach=coach)


_HOST = _Host()


def create_stream(
    take_id: uuid.UUID,
    *,
    owner_id: uuid.UUID,
    stt_adapter: SttAdapter,
    config: SttConfig,
    store: TranscriptStore,
    segment_no: int = 1,
    stt_session_no: int = 0,
    grace_sec: float | None = None,
    auto_end: AutoEndPolicy | None = None,
    coach: CoachDeps | None = None,
) -> TakeStream:
    """Take 의 스트림을 만든다. Take 별 기록을 찾거나 만들어 넘긴다.

    레지스트리(`state.streams`)에는 `attach` 가 넣는다.
    """
    policy = AutoEndPolicy.from_settings() if auto_end is None else auto_end
    return TakeStream(
        take_id,
        owner_id=owner_id,
        stt_adapter=stt_adapter,
        config=config,
        store=store,
        record=state.take_record(take_id, policy, store),
        host=_HOST,
        segment_no=segment_no,
        stt_session_no=stt_session_no,
        grace_sec=grace_sec,
        auto_end=policy,
        coach=coach,
    )


async def attach(
    take_id: uuid.UUID,
    ws: WebSocket,
    *,
    owner_id: uuid.UUID,
    stt_adapter: SttAdapter,
    config: SttConfig,
    store: TranscriptStore,
    coach: CoachDeps | None = None,
    coach_context: TakeContext | None = None,
) -> TakeStream:
    """Take 의 스트림을 찾거나 만들고 이 연결을 붙인다.

    Take 존재·소유·상태는 호출자(service)가 DB 로 먼저 검사한다. 여기서는 스트림을
    만든 사용자와 다르면 `StreamOwnerMismatch` — 그 검사의 이중 안전장치다.
    이 프로세스에서 끝낸 Take 면 `TakeEnded`. 새 스트림을 열 자리가 없으면 `TooManyStreams`
    (`_make_room`).

    `coach_context` 는 Take 를 시작할 때 읽은 값이다. 코치가 아직 꺼져 있으면 이 값으로 켠다 —
    다시 붙은 연결이면 이미 켜져 있고 그 `coach_state` 를 이어 간다. 없으면(읽기 실패) STT 만 돈다.
    """
    async with state.attach_lock(take_id):
        # 정리 중인 스트림을 기다리기 전에 본다. 끝낸 Take 라면 기다렸다 새로 열 이유가 없다.
        # 정리 중인 스트림은 새로 끝나지 않으므로(끝낼 때 정리보다 먼저 적는다) 한 번이면 된다
        if take_id in state.ended:
            raise TakeEnded(state.ended[take_id])

        stream = state.streams.get(take_id)

        if stream is not None and stream.is_stopping:
            # 정리 중인 스트림에 붙이면 곧 사라질 객체에 오디오를 밀어 넣게 된다.
            # 끝나기를 기다렸다가 새로 만든다. 정상 종료(stop)와 같은 시간을 기다린다 —
            # 더 일찍 끊으면 Deepgram 이 아직 보내지 않은 마지막 문장이 사라진다
            if not await stream.wait_closed():
                # 그래도 안 끝나면 강제로 끊고, 끊긴 스트림이 실제로 끝나기를 기다린다.
                # forget() 만 하면 옛 스트림은 Deepgram 연결을 붙든 채 계속 돈다. 끝나기 전에
                # 아래에서 번호를 읽으면 옛 스트림이 저장 중인 seq 를 새 스트림이 또 쓴다
                stream.cancel()
                await stream.wait_cancelled(stt_loop.ABORT_TIMEOUT_SEC)
            forget(stream)
            stream = None

        if stream is not None and stream.is_finished:
            forget(stream)
            stream = None

        if stream is None:
            # 이 프로세스가 처음 보는 Take 면(앱이 Take 도중에 다시 떴음) 적어 둔 문장 끝 신호를
            # 이어 받는다
            restored = (
                [] if take_id in state.takes else await _stored_utterance_ends(take_id, store)
            )
            # 저장된 마지막 번호에서 이어 받는다. 이 스트림이 처음이면 (0, 0) 이라 1·0 부터
            last_seq, last_session_no = await store.cursor(take_id)
            # 자리 확보 중에는 옛 Deepgram 세션이 실제로 닫히기를 기다린다. 같은 사용자의
            # 다른 Take도 이 확인부터 등록까지 함께 잠가 빈자리를 중복으로 쓰지 못하게 한다.
            async with state.stream_owner_lock(owner_id):
                try:
                    await _make_room(owner_id)
                except TooManyStreams:
                    if not abandon.armed(take_id):
                        # 첫 연결이 Take 를 시작했는데(`RUNNING`) 스트림이 없다. 아무도 다시
                        # 붙지 않으면 떠난 것으로 끝낸다 — 스트림이 닫힌 Take 와 같다
                        arm_abandon(take_id, store, closed_at=datetime.now(UTC))
                    raise
                stream = create_stream(
                    take_id,
                    owner_id=owner_id,
                    stt_adapter=stt_adapter,
                    config=config,
                    store=store,
                    segment_no=last_seq + 1,
                    stt_session_no=last_session_no,
                    coach=coach,
                )
                state.streams[take_id] = stream
                for ms in restored:
                    stream._record.coach.on_utterance_end(ms)
                stream.start()
        elif stream.owner_id != owner_id:
            raise StreamOwnerMismatch
        if coach is not None and coach_context is not None:
            stream._record.coach.enable(coach_context, client=coach.client, store=coach.store)

    await stream.attach(ws)
    return stream


async def _make_room(owner_id: uuid.UUID) -> None:
    """이 사용자의 새 스트림 자리를 만든다. 열린 스트림이 `MAX_STREAMS_PER_USER` 개면 연결이 끊겨
    다시 붙기를 기다리는(grace) 스트림과 오디오가 멈춘 연결(`is_idle`)의 스트림을 오래된 것부터
    정리한다. 그래도 차 있으면 `TooManyStreams`.

    오디오가 오는 연결의 스트림은 정리하지 않는다 — 다른 창에서 발표 중일 수 있다. 정리한 스트림의
    Take 는 끝나지 않는다. grace 가 만료된 것과 같아 다시 붙으면 새 스트림이 번호를 이어 받는다.
    정리 중인 스트림도 실제로 닫힐 때까지 센다. request_stop만 하고 자리에서 빼면 반복해서
    끊고 붙이는 동안 drain 중인 Deepgram 세션이 상한 밖으로 계속 쌓인다.
    """
    open_streams = _open_streams(owner_id)
    excess = len(open_streams) - MAX_STREAMS_PER_USER + 1
    if excess <= 0:
        return
    # 맵은 넣은 순서다. 앞이 오래된 것이다
    waiting = [stream for stream in open_streams if _reclaimable(stream)]
    if len(waiting) < excess:
        logger.warning(
            "사용자의 스트림이 상한이라 새 연결을 거절한다 user=%s limit=%d",
            owner_id,
            MAX_STREAMS_PER_USER,
        )
        raise TooManyStreams
    for stream in waiting[:excess]:
        logger.info(
            "새 스트림 자리를 만들려고 끊겼거나 오디오가 멈춘 스트림을 정리한다 take=%s",
            stream.take_id,
        )
        if stream.has_client and not stream.is_stopping:
            # 오디오가 멈춘 연결이다. 그 연결을 닫고 정리한다
            stream.release_idle()
        else:
            stream.request_stop()
        if not await stream.wait_closed():
            stream.cancel()
            # 정리가 끝나지 않았으면 레지스트리에 남겨 다음 요청에서도 세고 새 세션은 거절한다.
            if not await stream.wait_closed(stt_loop.ABORT_TIMEOUT_SEC):
                raise TooManyStreams


def has_room(owner_id: uuid.UUID, take_id: uuid.UUID) -> bool:
    """이 사용자가 그 Take 의 스트림을 열 자리가 있다. `_make_room` 이 비울 수 있는 자리도 센다.

    첫 연결이 Take 를 시작(`RUNNING`)하기 전에 묻는다 — 거절할 Take 를 시작해 두면 떠난 Take 로
    끝나 기록에 남는다. 잠그지 않고 보므로 그 사이 다른 Take 가 자리를 차지하면 `attach` 가
    거절한다.
    """
    blocking = [
        stream
        for stream in _open_streams(owner_id)
        if stream.take_id != take_id and not _reclaimable(stream)
    ]
    return len(blocking) < MAX_STREAMS_PER_USER


def _open_streams(owner_id: uuid.UUID) -> list[TakeStream]:
    return [
        stream
        for stream in state.streams.values()
        if stream.owner_id == owner_id and not stream.is_finished
    ]


def _reclaimable(stream: TakeStream) -> bool:
    """새 스트림 자리를 위해 정리해도 되는 스트림 — 정리 중이거나 다시 붙기를 기다리거나 붙은
    연결의 오디오가 멈췄다."""
    return stream.is_stopping or not stream.has_client or stream.is_idle


async def _stored_utterance_ends(take_id: uuid.UUID, store: TranscriptStore) -> list[int]:
    """앞 프로세스가 내리며 적어 둔 문장 끝 신호 (`stop_all`). 앱이 Take 도중에 다시 떴을 때
    이어 받는다 — Take 를 끝낼 때 메모리의 목록으로 덮어 쓰므로 받지 않으면 앞부분이 빠진다."""
    try:
        return await store.load_utterance_ends(take_id)
    except Exception:
        # 발표는 이어 간다. 앞부분의 문장 끝 신호만 Take 결과(replay)에서 빠진다
        logger.exception("저장된 문장 끝 신호를 읽지 못했다 take=%s", take_id)
        return []


def forget(stream: TakeStream) -> None:
    if state.streams.get(stream.take_id) is stream:
        del state.streams[stream.take_id]


def coach_enabled(take_id: uuid.UUID) -> bool:
    """이 프로세스에서 그 Take 의 코치가 이미 켜져 있다 (다시 붙은 연결). 값을 다시 읽지 않는다."""
    record = state.takes.get(take_id)
    return record is not None and record.coach.enabled


def arm_abandon(
    take_id: uuid.UUID,
    store: TranscriptStore,
    *,
    closed_at: datetime,
    delay_sec: float | None = None,
    end_reason: LeftReason = "ABANDONED",
) -> None:
    """`delay_sec`(기본 `ABANDON_AFTER_SEC`) 뒤에 Take 를 떠난 것으로 끝낸다 (`abandon.arm`). 걸려
    있으면 다시 건다.

    `closed_at` 은 마지막으로 연결이 끊긴 시각이다. Take 의 끝난 시각으로 적는다 — 기다린 시간까지
    연습한 시간에 넣지 않는다. `end_reason` 은 창을 닫았으면 `CLOSED`, 그 밖은 `ABANDONED` 다.
    """
    abandon.arm(
        take_id,
        closed_at=closed_at,
        on_expire=functools.partial(_on_abandon, store),
        delay_sec=delay_sec,
        end_reason=end_reason,
    )


def _on_abandon(
    store: TranscriptStore, take_id: uuid.UUID, closed_at: datetime, end_reason: LeftReason
) -> None:
    # 떠난 Take 타이머가 만료됐다. DB 를 기다려야 해서 태스크로 돈다. 타이머 콜백은 await 할 수 없다
    state.spawn(
        _end_abandoned(take_id, store, closed_at, end_reason), name=f"take-abandon:{take_id}"
    )


async def _end_abandoned(
    take_id: uuid.UUID, store: TranscriptStore, closed_at: datetime, end_reason: LeftReason
) -> None:
    # attach() 와 같은 락이다. 지금 다시 붙는 연결이 있으면 그쪽이 먼저 스트림을 열고, 끝내는 동안
    # 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with state.attach_lock(take_id):
        stream = state.streams.get(take_id)
        if take_id in state.ended or (stream is not None and not stream.is_finished):
            # 그 사이 끝났거나 (사용자 stop · 자동 종료 · Take PUT) 누가 다시 붙었다
            return
        record = state.takes.get(take_id)
        utterance_ends = finish.utterance_ends_of(take_id)
        try:
            marked = await asyncio.wait_for(
                store.mark_abandoned(take_id, closed_at, end_reason, utterance_ends),
                finish.MARK_AUTO_END_TIMEOUT_SEC,
            )
        except Exception:
            # 못 적으면 다음에 앱이 뜰 때 DB 를 보고 다시 건다
            logger.exception("떠난 Take 를 끝내지 못했다 take=%s", take_id)
            return
        if not marked:
            # 그 사이 Take 가 지워졌거나 이미 끝나 있었다
            logger.info("떠난 Take 를 적지 않았다 (Take 가 없거나 이미 끝남) take=%s", take_id)
            return
        state.ended[take_id] = end_reason
        if record is not None:
            # 떠난 Take 는 결과를 만들지 않는다. 끝내기 PUT 으로 되살아나면 replay 로 만든다 — 그때
            # 쓰도록 저장 못 한 코치 이벤트 · 1초 원자료를 넣고 버퍼를 닫은 뒤 기억을 놓는다
            await finish.seal_and_flush(take_id, None)
            finish.release_coach(take_id)
        logger.info(
            "떠난 Take 를 끝냈다 take=%s reason=%s closed_at=%s",
            take_id,
            end_reason,
            closed_at.isoformat(),
        )


async def arm_abandoned_takes(store: TranscriptStore) -> None:
    """앱이 뜰 때 부른다 (`main.lifespan`). 진행 중으로 남은 Take 마다 떠난 것으로 끝낼 타이머를
    다시 건다 (`abandon.arm_running_takes`)."""
    await abandon.arm_running_takes(store, on_expire=functools.partial(_on_abandon, store))


def is_live(take_id: uuid.UUID) -> bool:
    """이 Take 를 어느 창에서 발표 중이다 (`TakeStream.is_receiving_audio`)."""
    stream = state.streams.get(take_id)
    return stream is not None and stream.is_receiving_audio


async def end_closed_tab(take_id: uuid.UUID) -> None:
    """창을 다시 연 FE 가 Take 를 실패(`CLOSED`)로 끝냈다. DB 에 적은 뒤 부른다.

    떠난 것으로 끝낼 타이머를 끄고, 같은 Take 로 다시 붙지 못하게 하고, 남은 스트림을 끊는다.
    브라우저가 닫힘 프레임 없이 사라졌으면 서버는 아직 그 연결이 붙어 있다고 믿는다 — 그 연결도
    끊는다.
    """
    # attach() 와 같은 락이다. 끊는 동안 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with state.attach_lock(take_id):
        # 끝낸 것으로 먼저 적는다. 그래야 지금 닫히는 스트림이 타이머를 다시 걸지 않는다
        if take_id not in state.ended:
            state.ended[take_id] = "CLOSED"
        abandon.cancel(take_id)
        stream = state.streams.get(take_id)
    # 창을 닫은 Take 는 결과를 만들지 않는다. 스트림이 이미 정리됐어도 코치의 기억은 남아 있다
    finish.release_coach(take_id)
    if stream is not None:
        stream.drop_closed_tab()


async def discard(take_ids: Iterable[uuid.UUID]) -> None:
    """지운 Take 들의 스트림을 끊는다(`TakeStream.discard`). 스트림이 없는 Take 는 건너뛴다.

    Take 를 지우는 요청이 커밋한 뒤에 부른다. 먼저 끊으면 커밋이 실패했을 때 지워지지도 않은
    연습의 STT 만 끊긴다.
    """
    for take_id in take_ids:
        # 떠난 뒤 지운 Take 다. 끝낼 것이 없다
        abandon.cancel(take_id)
        # attach() 가 이 Take 의 스트림을 만드는 중이면 끝나기를 기다린다. 안 그러면 방금 만든
        # 스트림을 못 보고 지나간다
        async with state.attach_lock(take_id):
            stream = state.streams.get(take_id)
        finish.release_coach(take_id)
        if stream is not None:
            stream.discard()


async def sign_out(user_id: uuid.UUID) -> None:
    """로그아웃한 사용자의 연결을 `UNAUTHORIZED` 로 닫는다(`TakeStream.sign_out`).

    WS 는 붙을 때만 Access 토큰을 본다. 닫지 않으면 로그아웃 뒤에도 발표 스트림이 최대 1시간
    이어진다. Take 는 끝내지 않는다 — 끊긴 연결로 보고 grace · 떠난 Take 규칙을 따른다.
    Access 토큰에 기기 정보가 없어 다른 기기의 연결도 닫힌다. FE 는 `UNAUTHORIZED` 를 받으면 토큰을
    새로 받아 다시 붙으므로 그 기기는 이어지고, 로그아웃한 기기는 새로 받지 못해 다시 붙지 못한다.
    끝내는 중인 스트림은 건너뛴다. 연결이 곧 닫히고, 여기서 닫으면 FE 가 끝내는 도중에 다시
    붙으려 한다.
    """
    streams = [
        stream
        for stream in state.streams.values()
        if stream.owner_id == user_id and not stream.is_stopping
    ]
    await asyncio.gather(*(stream.sign_out() for stream in streams))


async def _save_utterance_ends(take_id: uuid.UUID, record: TakeRecord) -> None:
    ends = record.coach.utterance_ends
    if not ends:
        return
    try:
        await asyncio.wait_for(
            record.store.save_utterance_ends(take_id, ends), finish.FLUSH_METRICS_TIMEOUT_SEC
        )
    except Exception:
        logger.exception("진행 중인 Take 의 문장 끝 신호를 적어 두지 못했다 take=%s", take_id)


def active_count() -> int:
    return len(state.streams)


async def stop_all() -> None:
    """앱 종료. 남은 스트림을 정리한다.

    정상 종료(CloseStream) 를 잠깐 기다렸다가 안 끝나면 끊는다. 끊은 뒤에도 정리
    (Deepgram 소켓 닫기) 가 끝나기를 기다린다 — 여기서 바로 돌아가면 이벤트 루프가
    닫히면서 "Task was destroyed but it is pending" 로 정리가 중간에 잘린다.
    """
    streams = list(state.streams.values())
    state.streams.clear()
    for stream in streams:
        stream.request_stop()
    for stream in streams:
        if not await stream.wait_closed(timeout=2.0):
            stream.cancel()
            await stream.wait_closed(timeout=stt_loop.ABORT_TIMEOUT_SEC)
    # 닫힌 스트림이 남긴 1초 원자료. 다시 뜨면 메모리에 없다. 스트림이 닫히며 시작한 백그라운드
    # 저장은 행을 꺼내 가 버퍼가 비어 보이므로 `pending` 으로 고른다
    unsaved = [
        take_id
        for take_id, record in list(state.takes.items())
        if record.metrics.pending or record.slides.pending
    ]
    # 진행 중인 Take 의 문장 끝 신호는 Take 를 끝낼 때 한 번에 적는다. 다시 뜬 프로세스가 이어
    # 받도록(`attach`) 지금 적어 둔다
    live = [
        (take_id, record)
        for take_id, record in list(state.takes.items())
        if not record.coach.released
    ]
    await asyncio.gather(
        *(finish.flush_metrics(take_id) for take_id in unsaved),
        *(_save_utterance_ends(take_id, record) for take_id, record in live),
    )
    # 진행 중인 코치 판단을 끊고 저장 못 한 코치 이벤트를 넣는다. 다시 뜨면 메모리에 없다
    await asyncio.gather(
        *(
            record.coach.shutdown(finish.FLUSH_METRICS_TIMEOUT_SEC)
            for record in list(state.takes.values())
        )
    )
    if state.background:
        # Take 끝내기 정리(자동 종료 · 떠난 Take · 늦은 종료 기록)가 아직 돌고 있을 수 있다.
        # 여기서 잘리면 Take 가 진행 중으로 남아 다시 뜬 뒤 떠난 Take 로 끝난다. 스트림은 위에서 다
        # 닫혔으므로 정리에 남은 것은 FE 의 마지막 1초 조각 기다리기, 재료 만들기와 원자료 저장(같이
        # 돈다), DB 에 적기, FE 에 closed 를 보내고 닫기다. 다시 적으려고 쉬는 중인 것은 상한에서
        # 끊긴다
        finishing_sec = (
            runtime.FINAL_METRICS_WAIT_SEC
            + max(finish.FLUSH_METRICS_TIMEOUT_SEC, 2 * SEAL_WAIT_SEC)
            + finish.MARK_AUTO_END_TIMEOUT_SEC
            + stt_loop.ABORT_TIMEOUT_SEC
        )
        _, pending = await asyncio.wait(state.background.tasks(), timeout=finishing_sec)
        for task in pending:
            # 루프가 닫히며 "pending 인 채로 사라졌다" 로만 남지 않게, 무엇이 남았는지 적고 끊는다
            logger.error("종료 때까지 끝나지 않은 백그라운드 태스크를 끊는다 %s", task.get_name())
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=stt_loop.ABORT_TIMEOUT_SEC)
