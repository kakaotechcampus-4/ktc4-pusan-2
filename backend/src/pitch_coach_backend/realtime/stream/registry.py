"""Take 별 스트림 · 기록의 레지스트리. 프로세스에 하나이고 요청 · 연결 · 재연결이 같이 쓴다.

연결(`connection/session.py`)은 연결마다 새로 만들지만, 그 연결은 여기서 같은 Take 의 스트림을
찾는다 (`attach`). 스트림이 바뀌어도 이어질 Take 별 기록(말 없음 · 서버 시계 · 1초 원자료 · 코치,
`runtime.TakeRecord`)과 이 프로세스에서 끝낸 Take(`_ended`)도 여기 둔다. 스트림은 이 맵 · 잠금을
직접 만지지 않고 `runtime.StreamHost` 로 알린다.

Take 를 끝내는 일(마지막 창 · 원자료 저장 · DB 에 적기 · Take 결과 띄우기, `finish_take`)과 떠난
Take 를 끝내는 일(`_end_abandoned`)도 여기서 한다. 떠난 Take 타이머 자체는 `abandon.py` 다.

앱 수명: `main.lifespan` 이 뜰 때 `arm_abandoned_takes` 로 떠난 Take 타이머를 다시 걸고, 내릴 때
`stop_all` 로 스트림 drain · 기록 flush · 백그라운드 태스크 정리를 기다린다.

uvicorn 워커 하나 · 프로세스 하나가 전제다. 워커를 늘리면 레지스트리가 프로세스마다 생겨 같은 Take
의 연결이 다른 스트림에 붙는다. 그때는 스트림 소유권 · 요청 라우팅 · 공유 상태를 따로 설계한다.
"""

import asyncio
import contextlib
import functools
import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Coroutine, Hashable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket

from pitch_coach_backend.coach import finalize as coach_finalize
from pitch_coach_backend.coach.requests import TakeContext
from pitch_coach_backend.coach.session import (
    SEAL_WAIT_SEC,
    CoachSession,
    FinalizeSnapshot,
    being_cancelled,
)
from pitch_coach_backend.core.retry import retry_transient
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.realtime.dto import (
    AutoEndReason,
)
from pitch_coach_backend.realtime.storage.metrics import MetricBuffer, SlideBuffer
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream import abandon, runtime
from pitch_coach_backend.realtime.stream.abandon import LeftReason
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.stream.runtime import CoachDeps, TakeRecord, TakeStream
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttAdapter,
    SttConfig,
)

logger = logging.getLogger(__name__)


# 자동 종료를 DB 에 적는 상한. DB 가 늦어도 연결 정리까지 붙잡히지 않게 한다
MARK_AUTO_END_TIMEOUT_SEC = 5.0


# Take 종료를 DB 에 적지 못했을 때 다시 적는 간격(초). 사용자가 끝낸 Take 라 DB 가 돌아오면 적고
# Take 결과를 띄운다. 다 써도 안 되면 코치의 기억(finalize 재료)을 든 채 끝내기 PUT 을 기다린다
MARK_RETRY_SEC = (2.0, 5.0, 15.0, 30.0, 60.0, 120.0)


# Take 를 끝내기 전에 남은 1초 원자료를 저장하는 상한. 위와 같은 이유다
FLUSH_METRICS_TIMEOUT_SEC = 5.0


# 끝낸 Take · Take 별 자동 종료 상태를 프로세스 안에서 기억하는 개수. 오래된 것부터 버린다
MAX_REMEMBERED_TAKES = 10_000


class StreamOwnerMismatch(Exception):
    """다른 사용자가 이미 쓰고 있는 Take 다."""


class TakeEnded(Exception):
    """이 프로세스에서 끝낸 Take 다. DB 에 적히기 전이라도 다시 붙지 못한다.

    BE 가 끝냈으면 `reason` 에 이유가 있다 (자동 종료 · 떠남). 사용자가 끝냈으면 None.
    """

    def __init__(self, reason: AutoEndReason | LeftReason | None = None) -> None:
        super().__init__(reason)
        self.reason = reason


# 돌아가는 백그라운드 태스크 (자동 종료 · 떠난 Take 정리 · 늦은 종료 기록). 실패는 이름과 함께
# 로그에 남는다
_background = TaskRegistry()


def _spawn(
    coro: Coroutine[object, object, None], *, name: str, key: Hashable | None = None
) -> None:
    _background.spawn(coro, name=name, key=key)


# ── 레지스트리 ────────────────────────────────────────────────────────
_streams: dict[uuid.UUID, TakeStream] = {}


# attach() 의 "확인 -> 생성" 이 원자적이어야 한다. 아니면 grace 만료 직후처럼 같은
# take_id 로 재연결이 동시에 두 개 오는 순간 **둘 다** `_streams.get()` 에서 "없다" 를
# 보고 각자 TakeStream·Deepgram 세션을 만든다. 그러면 하나는 `_streams` 덮어쓰기로
# 레지스트리에서 밀려나 고아가 되고, Deepgram 연결은 둘 생긴다.
#
# 락은 **take_id 별**이다. 프로세스 전체 락 하나면 is_stopping 핸드오버(최대 DRAIN_TIMEOUT_SEC
# + ABORT_TIMEOUT_SEC) 나 커서 조회(DB 왕복) 동안 다른 Take 의 연결까지 같이 멈춘다 —
# 동시 사용자가 늘면 그 지연이 체감된다 (코드 리뷰 지적). 다른 Take 끼리는 공유하는
# 상태가 없으니 서로 기다릴 이유가 없다. 락 객체는 쓰는 동안만 살아 있고 마지막으로
# 나가는 쪽이 치운다 — Take 마다 락이 영영 남지 않게.
@dataclass
class _KeyedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 락을 잡았거나 기다리는 코루틴 수. asyncio 는 단일 스레드라 await 사이에서만 바뀐다
    holders: int = 0


_attach_locks: dict[uuid.UUID, _KeyedLock] = {}


class _BoundedDict[K, V](OrderedDict[K, V]):
    """`MAX_REMEMBERED_TAKES` 개까지만 둔다. 넣거나 고친 것이 가장 새것이고, 오래된 것부터 버린다.

    값은 `d[k] = v` 로만 넣는다 — `setdefault` · `update` 는 C 구현이라 여기를 거치지 않는다.
    """

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > MAX_REMEMBERED_TAKES:
            self.popitem(last=False)


# 끝낸 Take. 값은 BE 가 끝낸 이유(자동 종료 · 떠남)고, 사용자가 끝냈으면 None 이다.
# DB(`takes.status` · `end_reason`)가 원본이고 이건 그게 적히기 전의 틈을 막는다 — 마지막 확정
# 전사를 저장하고 DB 에 적기 전에 FE 가 다시 붙으면 새 스트림이 열린다. 프로세스가 다시 뜨면
# 비지만 그때는 DB 가 막는다
_ended: _BoundedDict[uuid.UUID, AutoEndReason | LeftReason | None] = _BoundedDict()

_takes: _BoundedDict[uuid.UUID, TakeRecord] = _BoundedDict()


def _take_record(take_id: uuid.UUID, policy: AutoEndPolicy, store: TranscriptStore) -> TakeRecord:
    record = _takes.get(take_id)
    if record is None:
        record = TakeRecord(
            silence=SilenceTracker(policy),
            metrics=MetricBuffer(take_id, store),
            slides=SlideBuffer(take_id, store),
            coach=CoachSession(take_id),
            store=store,
        )
    # 다시 넣어 가장 새것으로 둔다. 진행 중인 Take 가 오래된 순으로 밀려나지 않게
    _takes[take_id] = record
    return record


class _Host:
    """스트림이 알리는 자리 (`runtime.StreamHost`). 이 파일의 맵 · 타이머 · 태스크로 이어 준다."""

    def stream_started(self, take_id: uuid.UUID) -> None:
        abandon.cancel(take_id)

    def stream_closed(
        self, stream: TakeStream, store: TranscriptStore, *, discarded: bool, tab_closed: bool
    ) -> None:
        forget(stream)
        if discarded or stream.take_id in _ended:
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
            _ended[take_id] = reason
        elif take_id not in _ended:
            _ended[take_id] = None

    def spawn(self, coro: Coroutine[object, object, None], *, name: str) -> None:
        _spawn(coro, name=name)

    async def finish_take(
        self,
        take_id: uuid.UUID,
        store: TranscriptStore,
        *,
        end_reason: str | None,
        coach: CoachDeps | None,
    ) -> bool:
        return await finish_take(take_id, store, end_reason=end_reason, coach=coach)


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

    레지스트리(`_streams`)에는 `attach` 가 넣는다.
    """
    policy = AutoEndPolicy.from_settings() if auto_end is None else auto_end
    return TakeStream(
        take_id,
        owner_id=owner_id,
        stt_adapter=stt_adapter,
        config=config,
        store=store,
        record=_take_record(take_id, policy, store),
        host=_HOST,
        segment_no=segment_no,
        stt_session_no=stt_session_no,
        grace_sec=grace_sec,
        auto_end=policy,
        coach=coach,
    )


@contextlib.asynccontextmanager
async def _attach_lock(take_id: uuid.UUID):
    entry = _attach_locks.setdefault(take_id, _KeyedLock())
    entry.holders += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.holders -= 1
        if entry.holders == 0 and _attach_locks.get(take_id) is entry:
            del _attach_locks[take_id]


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
    이 프로세스에서 끝낸 Take 면 `TakeEnded`.

    `coach_context` 는 Take 를 시작할 때 읽은 값이다. 코치가 아직 꺼져 있으면 이 값으로 켠다 —
    다시 붙은 연결이면 이미 켜져 있고 그 `coach_state` 를 이어 간다. 없으면(읽기 실패) STT 만 돈다.
    """
    async with _attach_lock(take_id):
        # 정리 중인 스트림을 기다리기 전에 본다. 끝낸 Take 라면 기다렸다 새로 열 이유가 없다.
        # 정리 중인 스트림은 새로 끝나지 않으므로(끝낼 때 정리보다 먼저 적는다) 한 번이면 된다
        if take_id in _ended:
            raise TakeEnded(_ended[take_id])

        stream = _streams.get(take_id)

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
            restored = [] if take_id in _takes else await _stored_utterance_ends(take_id, store)
            # 저장된 마지막 번호에서 이어 받는다. 이 스트림이 처음이면 (0, 0) 이라 1·0 부터
            last_seq, last_session_no = await store.cursor(take_id)
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
            _streams[take_id] = stream
            for ms in restored:
                stream._record.coach.on_utterance_end(ms)
            stream.start()
        elif stream.owner_id != owner_id:
            raise StreamOwnerMismatch
        if coach is not None and coach_context is not None:
            stream._record.coach.enable(coach_context, client=coach.client, store=coach.store)

    await stream.attach(ws)
    return stream


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
    if _streams.get(stream.take_id) is stream:
        del _streams[stream.take_id]


async def flush_metrics(take_id: uuid.UUID) -> None:
    """Take 를 끝내기(`ANALYZING`) 직전이나 앱을 내릴 때 부른다. 남은 1초 원자료 · 장 변경을
    저장한다.

    Take 뒤 분석이 원자료를 읽으므로 끝난 것으로 적기 전에 다 있어야 한다. 이 뒤로 오는 것은 받지
    않는다 (`MetricBuffer.seal`). 못 넣어도 끝내기는 계속한다 — 못 넣은 초는 복구 때 빈 구간으로
    남는다. 백그라운드 저장이 돌고 있으면 그 저장이 끝나기를 기다린다 (같은 락).
    """
    record = _takes.get(take_id)
    if record is None:
        return
    buffers = ((record.metrics, "1초 원자료"), (record.slides, "장 변경"))
    for buffer, _ in buffers:
        buffer.seal()

    async def flush(buffer: MetricBuffer | SlideBuffer) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(buffer.flush(), FLUSH_METRICS_TIMEOUT_SEC)

    await asyncio.gather(*(flush(buffer) for buffer, _ in buffers))
    for buffer, what in buffers:
        # 못 넣은 행은 여기서 버린다. 끝난 뒤 다시 넣으면 Take 결과 저장과 엇갈린다 (`close`)
        dropped = buffer.close()
        if dropped:
            logger.error(
                "Take 를 끝내기 전에 %s 를 다 저장하지 못해 버린다 take=%s 개수=%d",
                what,
                take_id,
                dropped,
            )


async def seal_take(take_id: uuid.UUID) -> FinalizeSnapshot | None:
    """Take 를 끝낼 때 코치 판단을 멈추고 finalize 재료를 만든다 (1-3 3번 ①). 스트림을 닫은 뒤에
    부른다 — STT 를 닫은 뒤에 확정된 마지막 단어까지 마지막 창에 든다. 재료가 없으면 None."""
    record = _takes.get(take_id)
    if record is None:
        return None
    if record.seal is None:
        record.seal = asyncio.create_task(record.coach.seal(), name=f"coach-seal:{take_id}")
    seal = record.seal
    # 먼저 부른 쪽이 취소돼도 재료 만들기는 끝까지 간다. 뒤에 부른 쪽도 끝날 때까지 기다린다 —
    # 기다리지 않고 끝난 것으로 적으면 재료 없이 Take 결과를 띄운다
    try:
        return await asyncio.shield(seal)
    except asyncio.CancelledError:
        # 재료 만들기가 끊겼다. 이쪽이 취소된 게 아니면 재료 없이 끝내기를 이어 간다
        if being_cancelled():
            raise
    except Exception:
        logger.exception("Take 결과 재료를 만들지 못했다 take=%s", take_id)
    return None


def coach_enabled(take_id: uuid.UUID) -> bool:
    """이 프로세스에서 그 Take 의 코치가 이미 켜져 있다 (다시 붙은 연결). 값을 다시 읽지 않는다."""
    record = _takes.get(take_id)
    return record is not None and record.coach.enabled


def utterance_ends_of(take_id: uuid.UUID) -> list[int] | None:
    """Take 동안 모은 문장 끝 신호. 이 프로세스가 Take 를 모르면 None — 저장된 값을 그대로 둔다."""
    record = _takes.get(take_id)
    if record is None or record.coach.released:
        # 모르거나 이미 놓았다 (떠난 Take 는 끝낼 때 같이 썼다)
        return None
    return record.coach.utterance_ends


@dataclass(frozen=True)
class Ending:
    """Take 를 끝낼 때 DB 에 같이 적을 것(자동 종료 이유 · 문장 끝 신호)과 Take 결과의 재료."""

    end_reason: str | None
    utterance_ends: list[int] | None
    snapshot: FinalizeSnapshot | None


async def _seal_and_flush(take_id: uuid.UUID, end_reason: str | None) -> Ending:
    """끝난 것으로 적기 전에 마지막 창을 만들고(`seal_take`) 남은 원자료를 저장한다
    (`flush_metrics`). 둘은 같이 돈다."""
    record = _takes.get(take_id)
    if record is not None:
        # 원자료 버퍼와 코치가 같은 순간에 받기를 멈춘다. 저장되지 않은 1초가 마지막 창에 들거나
        # 마지막 창에 없는 1초가 저장되지 않게
        record.metrics.seal()
        record.slides.seal()
        record.coach.seal_inputs()
    snapshot, _ = await asyncio.gather(seal_take(take_id), flush_metrics(take_id))
    return Ending(end_reason, utterance_ends_of(take_id), snapshot)


def start_finalize(
    take_id: uuid.UUID, coach: CoachDeps | None, snapshot: FinalizeSnapshot | None
) -> None:
    """Take 를 `ANALYZING` · `FINALIZING` 으로 적은 뒤 부른다. 코치의 기억을 놓고 Take 결과를
    백그라운드로 띄운다. 재료가 없으면(다시 뜬 프로세스 · 되살린 Take) finalize 가 DB 에서 다시
    만든다."""
    release_coach(take_id)
    if coach is None:
        logger.warning("코치가 없어 Take 결과를 띄우지 못했다 take=%s", take_id)
        return
    coach_finalize.spawn(take_id, snapshot, client=coach.client, store=coach.store)


def after_mark(take_id: uuid.UUID, marked: bool, ending: Ending, coach: CoachDeps | None) -> None:
    """Take 종료를 DB 에 적은 뒤 부른다 (WS stop · 자동 종료 · 끝내기 PUT). 적었으면 Take 결과를
    띄운다. 못 적었으면(Take 가 없거나 이미 끝남) 먼저 적은 쪽이 띄웠다 — 코치의 기억만 놓는다."""
    if marked:
        start_finalize(take_id, coach, ending.snapshot)
        return
    logger.warning("Take 종료를 적지 않았다 (Take 가 없거나 이미 끝남) take=%s", take_id)
    release_coach(take_id)


async def finish_take(
    take_id: uuid.UUID,
    store: TranscriptStore,
    *,
    end_reason: str | None,
    coach: CoachDeps | None,
) -> bool:
    """스트림을 닫은 뒤 Take 를 끝낸다 (1-3 3번 ③ ④). WS stop 과 자동 종료가 부른다.

    마지막 창을 만들고, 원자료를 저장하고, Take 동안 모은 문장 끝 신호와 함께 `ANALYZING` ·
    `FINALIZING` 으로 적은 뒤 Take 결과를 띄운다 (`after_mark`). 바로 적었으면 True. Take 가 없거나
    이미 끝나 있었으면 False. DB 가 늦거나 실패하면 FE 를 붙잡지 않고 False 를 돌려준 뒤 뒤에서
    마저 적는다 (`mark_later`).
    """
    ending = await _seal_and_flush(take_id, end_reason)
    marking = asyncio.ensure_future(store.mark_ended(take_id, end_reason, ending.utterance_ends))
    try:
        # 상한을 넘겨도 적는 일은 멈추지 않는다 (DB 는 늦게라도 커밋한다)
        marked = await asyncio.wait_for(asyncio.shield(marking), MARK_AUTO_END_TIMEOUT_SEC)
    except Exception:
        logger.warning(
            "Take 종료 저장이 늦거나 실패했다. 뒤에서 마저 적는다 take=%s reason=%s",
            take_id,
            end_reason,
        )
        mark_later(take_id, store, ending, coach, marking=marking)
        return False
    after_mark(take_id, marked, ending, coach)
    return marked


def mark_later(
    take_id: uuid.UUID,
    store: TranscriptStore,
    ending: Ending,
    coach: CoachDeps | None,
    *,
    marking: Awaitable[bool] | None = None,
) -> None:
    """늦거나 실패한 Take 종료 기록을 뒤에서 마저 적는다. `marking` 은 먼저 시작한 기록이다.
    Take 마다 하나만 돈다 — 이미 돌고 있으면(끝내기 PUT 을 다시 보냄) 그쪽에 맡긴다.

    적히면 Take 결과를 띄운다 (`after_mark`). 그 사이 다른 쪽(끝내기 PUT 등)이 먼저 적었으면
    그쪽이 띄웠다. 끝내 못 적어도 코치의 기억(문장 끝 신호 · 재료)은 그대로 둔다 — 뒤에 끝내기 PUT
    이 오면 그 기억으로 적는다. 오지 않으면 Take 는 DB 에 진행 중으로 남아 앱이 다시 뜬 뒤 떠난
    Take 로 끝난다 (`arm_abandoned_takes`).
    """
    key = ("mark", take_id)
    if _background.running(key):
        return
    _spawn(
        _mark_later(take_id, store, ending, coach, marking=marking),
        name=f"take-mark:{take_id}",
        key=key,
    )


async def _mark_later(
    take_id: uuid.UUID,
    store: TranscriptStore,
    ending: Ending,
    coach: CoachDeps | None,
    *,
    marking: Awaitable[bool] | None,
) -> None:
    started = marking

    async def mark() -> bool:
        nonlocal started
        if started is None:
            return await store.mark_ended(take_id, ending.end_reason, ending.utterance_ends)
        first, started = started, None
        try:
            return await first
        except asyncio.CancelledError:
            if being_cancelled():
                raise
            # 먼저 시작한 기록이 끊겼다. DB 가 잠깐 안 된 것과 같게 다시 적는다
            raise TimeoutError("먼저 시작한 Take 종료 기록이 끊겼다") from None

    try:
        marked = await retry_transient(mark, MARK_RETRY_SEC, what=f"Take 종료 저장 take={take_id}")
    except Exception:
        logger.exception("Take 종료를 끝내 적지 못했다. 끝내기 PUT 을 기다린다 take=%s", take_id)
        return
    after_mark(take_id, marked, ending, coach)


def release_coach(take_id: uuid.UUID) -> None:
    """Take 가 끝나 코치의 기억과 finalize 재료를 놓는다 (`coach_state` 는 15KB 안팎). 재료는 끝내는
    쪽이 `seal_take` 로 받아 각자 들고 있다."""
    record = _takes.get(take_id)
    if record is not None:
        record.seal = None
        record.coach.release()


async def end_without_stop(take_id: uuid.UUID) -> Ending | None:
    """WS `stop` 없이 Take 를 끝낸다 (FE 종료 흐름의 Take PUT). 지금 DB 에 적어도 되면 같이 적을
    것을 돌려준다. 적는 일은 부른 쪽이 하고 그 뒤 `after_mark` 를 부른다.

    발표 중에 재연결을 포기한 FE 는 소켓을 정리해 `stop` 을 보내지 못한다. 그래도 발표는 끝까지
    가므로 REST 로 끝낼 때 여기를 거친다. 전사 순서를 지키려고 셋으로 나눈다.

    - `stop` 으로 끝내는 중이거나 연결이 붙어 있으면 None. 그 연결이 마지막 전사를 저장한 뒤 적는다
    - 연결 없이 남은 스트림(끊긴 뒤 30초)은 `stop` 처럼 남은 전사 · 1초 원자료까지 저장하고 정리한
      뒤 돌려준다
    - 스트림이 없으면 남은 1초 원자료만 저장하고 돌려준다

    돌려주면 이 뒤로 같은 Take 로 붙는 연결은 `TakeEnded` 로 거절한다.
    """
    # attach() 와 같은 락이다. 정리하는 동안 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with _attach_lock(take_id):
        stream = _streams.get(take_id)
        if stream is not None and (take_id in _ended or stream.has_client):
            return None
        reason = _ended.get(take_id)
        if isinstance(reason, AutoEndReason):
            # 자동 종료가 끝내는 중이다 (그쪽 기록이 늦어 뒤에서 다시 적는 중일 수 있다). 누가
            # 먼저 적든 이유가 남도록 같은 이유로 적는다
            end_reason: str | None = reason.value
        else:
            # 사용자가 끝냈다. 떠난 것으로 끝낸 Take 면 되살린다 (이유를 지운다)
            _ended[take_id] = None
            end_reason = None
        if stream is not None:
            stream.end()
            if not await stream.wait_closed():
                logger.warning("Deepgram drain 타임아웃 take=%s. 스트림을 끊는다", take_id)
                stream.cancel()
                await stream.wait_cancelled(stt_loop.ABORT_TIMEOUT_SEC)
        # 마지막 창을 만들고, 연결 없이 정리된 스트림이 못 넣은 행을 넣는다
        return await _seal_and_flush(take_id, end_reason)


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
    _spawn(_end_abandoned(take_id, store, closed_at, end_reason), name=f"take-abandon:{take_id}")


async def _end_abandoned(
    take_id: uuid.UUID, store: TranscriptStore, closed_at: datetime, end_reason: LeftReason
) -> None:
    # attach() 와 같은 락이다. 지금 다시 붙는 연결이 있으면 그쪽이 먼저 스트림을 열고, 끝내는 동안
    # 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with _attach_lock(take_id):
        stream = _streams.get(take_id)
        if take_id in _ended or (stream is not None and not stream.is_finished):
            # 그 사이 끝났거나 (사용자 stop · 자동 종료 · Take PUT) 누가 다시 붙었다
            return
        record = _takes.get(take_id)
        utterance_ends = utterance_ends_of(take_id)
        try:
            marked = await asyncio.wait_for(
                store.mark_abandoned(take_id, closed_at, end_reason, utterance_ends),
                MARK_AUTO_END_TIMEOUT_SEC,
            )
        except Exception:
            # 못 적으면 다음에 앱이 뜰 때 DB 를 보고 다시 건다
            logger.exception("떠난 Take 를 끝내지 못했다 take=%s", take_id)
            return
        if not marked:
            # 그 사이 Take 가 지워졌거나 이미 끝나 있었다
            logger.info("떠난 Take 를 적지 않았다 (Take 가 없거나 이미 끝남) take=%s", take_id)
            return
        _ended[take_id] = end_reason
        if record is not None:
            # 떠난 Take 는 결과를 만들지 않는다. 끝내기 PUT 으로 되살아나면 replay 로 만든다 — 그때
            # 쓰도록 저장 못 한 코치 이벤트 · 1초 원자료를 넣고 버퍼를 닫은 뒤 기억을 놓는다
            await _seal_and_flush(take_id, None)
            release_coach(take_id)
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
    stream = _streams.get(take_id)
    return stream is not None and stream.is_receiving_audio


async def end_closed_tab(take_id: uuid.UUID) -> None:
    """창을 다시 연 FE 가 Take 를 실패(`CLOSED`)로 끝냈다. DB 에 적은 뒤 부른다.

    떠난 것으로 끝낼 타이머를 끄고, 같은 Take 로 다시 붙지 못하게 하고, 남은 스트림을 끊는다.
    브라우저가 닫힘 프레임 없이 사라졌으면 서버는 아직 그 연결이 붙어 있다고 믿는다 — 그 연결도
    끊는다.
    """
    # attach() 와 같은 락이다. 끊는 동안 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with _attach_lock(take_id):
        # 끝낸 것으로 먼저 적는다. 그래야 지금 닫히는 스트림이 타이머를 다시 걸지 않는다
        if take_id not in _ended:
            _ended[take_id] = "CLOSED"
        abandon.cancel(take_id)
        stream = _streams.get(take_id)
    # 창을 닫은 Take 는 결과를 만들지 않는다. 스트림이 이미 정리됐어도 코치의 기억은 남아 있다
    release_coach(take_id)
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
        async with _attach_lock(take_id):
            stream = _streams.get(take_id)
        release_coach(take_id)
        if stream is not None:
            stream.discard()


async def _save_utterance_ends(take_id: uuid.UUID, record: TakeRecord) -> None:
    ends = record.coach.utterance_ends
    if not ends:
        return
    try:
        await asyncio.wait_for(
            record.store.save_utterance_ends(take_id, ends), FLUSH_METRICS_TIMEOUT_SEC
        )
    except Exception:
        logger.exception("진행 중인 Take 의 문장 끝 신호를 적어 두지 못했다 take=%s", take_id)


def active_count() -> int:
    return len(_streams)


async def stop_all() -> None:
    """앱 종료. 남은 스트림을 정리한다.

    정상 종료(CloseStream) 를 잠깐 기다렸다가 안 끝나면 끊는다. 끊은 뒤에도 정리
    (Deepgram 소켓 닫기) 가 끝나기를 기다린다 — 여기서 바로 돌아가면 이벤트 루프가
    닫히면서 "Task was destroyed but it is pending" 로 정리가 중간에 잘린다.
    """
    streams = list(_streams.values())
    _streams.clear()
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
        for take_id, record in list(_takes.items())
        if record.metrics.pending or record.slides.pending
    ]
    # 진행 중인 Take 의 문장 끝 신호는 Take 를 끝낼 때 한 번에 적는다. 다시 뜬 프로세스가 이어
    # 받도록(`attach`) 지금 적어 둔다
    live = [
        (take_id, record) for take_id, record in list(_takes.items()) if not record.coach.released
    ]
    await asyncio.gather(
        *(flush_metrics(take_id) for take_id in unsaved),
        *(_save_utterance_ends(take_id, record) for take_id, record in live),
    )
    # 진행 중인 코치 판단을 끊고 저장 못 한 코치 이벤트를 넣는다. 다시 뜨면 메모리에 없다
    await asyncio.gather(
        *(record.coach.shutdown(FLUSH_METRICS_TIMEOUT_SEC) for record in list(_takes.values()))
    )
    if _background:
        # Take 끝내기 정리(자동 종료 · 떠난 Take · 늦은 종료 기록)가 아직 돌고 있을 수 있다.
        # 여기서 잘리면 Take 가 진행 중으로 남아 다시 뜬 뒤 떠난 Take 로 끝난다. 스트림은 위에서 다
        # 닫혔으므로 정리에 남은 것은 FE 의 마지막 1초 조각 기다리기, 재료 만들기와 원자료 저장(같이
        # 돈다), DB 에 적기, FE 에 closed 를 보내고 닫기다. 다시 적으려고 쉬는 중인 것은 상한에서
        # 끊긴다
        finishing_sec = (
            runtime.FINAL_METRICS_WAIT_SEC
            + max(FLUSH_METRICS_TIMEOUT_SEC, 2 * SEAL_WAIT_SEC)
            + MARK_AUTO_END_TIMEOUT_SEC
            + stt_loop.ABORT_TIMEOUT_SEC
        )
        _, pending = await asyncio.wait(_background.tasks(), timeout=finishing_sec)
        for task in pending:
            # 루프가 닫히며 "pending 인 채로 사라졌다" 로만 남지 않게, 무엇이 남았는지 적고 끊는다
            logger.error("종료 때까지 끝나지 않은 백그라운드 태스크를 끊는다 %s", task.get_name())
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=stt_loop.ABORT_TIMEOUT_SEC)
