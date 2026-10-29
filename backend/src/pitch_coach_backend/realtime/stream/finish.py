"""Take 를 끝내는 일. 마지막 창을 만들고 남은 원자료를 저장한 뒤 DB 에 적고 Take 결과를 띄운다.

WS stop(`connection/session.py`) · 자동 종료(`record.StreamHost.finish_take`) · 끝내기 PUT
(`live_takes.py`, `end_without_stop`)이 부른다. DB 가 늦거나 실패하면 뒤에서 마저 적는다
(`mark_later`). 떠난 Take 는 registry 가 끝내며 원자료 저장(`seal_and_flush`)만 여기서 가져다 쓴다.

프로세스 상태(`state.py`)를 읽고 고친다. registry 를 import 하지 않는다.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass

from pitch_coach_backend.coach import finalize as coach_finalize
from pitch_coach_backend.coach.session import FinalizeSnapshot, being_cancelled
from pitch_coach_backend.core.retry import retry_transient
from pitch_coach_backend.realtime.dto import AutoEndReason
from pitch_coach_backend.realtime.storage.metrics import MetricBuffer, SlideBuffer
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream import state
from pitch_coach_backend.realtime.stream.record import CoachDeps
from pitch_coach_backend.realtime.stt import loop as stt_loop

logger = logging.getLogger(__name__)


# 자동 종료를 DB 에 적는 상한. DB 가 늦어도 연결 정리까지 붙잡히지 않게 한다
MARK_AUTO_END_TIMEOUT_SEC = 5.0


# Take 종료를 DB 에 적지 못했을 때 다시 적는 간격(초). 사용자가 끝낸 Take 라 DB 가 돌아오면 적고
# Take 결과를 띄운다. 다 써도 안 되면 코치의 기억(finalize 재료)을 든 채 끝내기 PUT 을 기다린다
MARK_RETRY_SEC = (2.0, 5.0, 15.0, 30.0, 60.0, 120.0)


# Take 를 끝내기 전에 남은 1초 원자료를 저장하는 상한. 위와 같은 이유다
FLUSH_METRICS_TIMEOUT_SEC = 5.0


async def flush_metrics(take_id: uuid.UUID) -> bool:
    """Take 를 끝내기(`ANALYZING`) 직전이나 앱을 내릴 때 부른다. 남은 1초 원자료 · 장 변경 ·
    확정 전사를 저장한다. 확정 전사를 다 저장했으면 True.

    Take 뒤 분석이 원자료를 읽으므로 끝난 것으로 적기 전에 다 있어야 한다. 이 뒤로 오는 것은 받지
    않는다 (`MetricBuffer.seal`). 1초 원자료 · 장 변경은 못 넣어도 끝내기는 계속한다 — 못 넣은 초는
    복구 때 빈 구간으로 남는다. 확정 전사는 리포트의 원천이라 버리지 않는다. 못 넣었으면 들고 있다가
    다시 넣고, 끝내는 쪽은 그 뒤에 끝난 것으로 적는다 (`save_finals`). 백그라운드 저장이 돌고 있으면
    그 저장이 끝나기를 기다린다 (같은 락).
    """
    record = state.takes.get(take_id)
    if record is None:
        return True
    buffers = ((record.metrics, "1초 원자료"), (record.slides, "장 변경"))
    for buffer, _ in buffers:
        buffer.seal()
    record.finals.seal()

    async def flush(buffer: MetricBuffer | SlideBuffer) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(buffer.flush(), FLUSH_METRICS_TIMEOUT_SEC)

    await asyncio.gather(*(flush(buffer) for buffer in (record.metrics, record.slides)))
    saved = await save_finals(take_id)
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

    return saved


async def save_finals(take_id: uuid.UUID) -> bool:
    """남은 확정 전사를 저장한다. 다 넣었으면 버퍼를 닫고 True. Take 를 끝낸 것으로 적기 전에
    부른다.

    못 넣었으면 그대로 들고 있다 — 버퍼가 뒤에서 다시 넣고(`RowBuffer`), 끝내는 쪽은 다시 부른 뒤
    적는다 (`mark_later`). 프로세스가 내려가면 잃는다.
    """
    record = state.takes.get(take_id)
    if record is None:
        return True
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(record.finals.flush(), FLUSH_METRICS_TIMEOUT_SEC)
    if record.finals.pending:
        logger.error(
            "확정 전사를 다 저장하지 못했다 take=%s 개수=%d",
            take_id,
            len(record.finals),
        )
        return False
    record.finals.close()
    return True


async def seal_take(take_id: uuid.UUID) -> FinalizeSnapshot | None:
    """Take 를 끝낼 때 코치 판단을 멈추고 finalize 재료를 만든다 (1-3 3번 ①). 스트림을 닫은 뒤에
    부른다 — STT 를 닫은 뒤에 확정된 마지막 단어까지 마지막 창에 든다. 재료가 없으면 None."""
    record = state.takes.get(take_id)
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


def utterance_ends_of(take_id: uuid.UUID) -> list[int] | None:
    """Take 동안 모은 문장 끝 신호. 이 프로세스가 Take 를 모르면 None — 저장된 값을 그대로 둔다."""
    record = state.takes.get(take_id)
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
    # 확정 전사를 다 저장했다. 아니면 지금 끝난 것으로 적지 않는다 — 분석이 빠진 전사로 돈다
    finals_saved: bool = True


async def seal_and_flush(take_id: uuid.UUID, end_reason: str | None) -> Ending:
    """끝난 것으로 적기 전에 마지막 창을 만들고(`seal_take`) 남은 원자료를 저장한다
    (`flush_metrics`). 둘은 같이 돈다."""
    record = state.takes.get(take_id)
    if record is not None:
        # 원자료 버퍼와 코치가 같은 순간에 받기를 멈춘다. 저장되지 않은 1초가 마지막 창에 들거나
        # 마지막 창에 없는 1초가 저장되지 않게
        record.metrics.seal()
        record.slides.seal()
        record.coach.seal_inputs()
    snapshot, finals_saved = await asyncio.gather(seal_take(take_id), flush_metrics(take_id))
    return Ending(end_reason, utterance_ends_of(take_id), snapshot, finals_saved)


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
    ending = await seal_and_flush(take_id, end_reason)
    if not ending.finals_saved:
        # 분석이 읽을 확정 전사가 아직 DB 에 없다. 지금 적으면 리포트에서 빠진다
        mark_later(take_id, store, ending, coach)
        return False
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
    if state.background.running(key):
        return
    state.spawn(
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
        if not await save_finals(take_id):
            # DB 가 아직 안 된다. 다음 간격에 전사부터 다시 넣는다
            raise TimeoutError("확정 전사를 아직 저장하지 못했다")
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
    record = state.takes.get(take_id)
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
    async with state.attach_lock(take_id):
        stream = state.streams.get(take_id)
        if stream is not None and (take_id in state.ended or stream.has_client):
            return None
        reason = state.ended.get(take_id)
        if isinstance(reason, AutoEndReason):
            # 자동 종료가 끝내는 중이다 (그쪽 기록이 늦어 뒤에서 다시 적는 중일 수 있다). 누가
            # 먼저 적든 이유가 남도록 같은 이유로 적는다
            end_reason: str | None = reason.value
        else:
            # 사용자가 끝냈다. 떠난 것으로 끝낸 Take 면 되살린다 (이유를 지운다)
            state.ended[take_id] = None
            end_reason = None
        if stream is not None:
            stream.end()
            if not await stream.wait_closed():
                logger.warning("Deepgram drain 타임아웃 take=%s. 스트림을 끊는다", take_id)
                stream.cancel()
                await stream.wait_cancelled(stt_loop.ABORT_TIMEOUT_SEC)
        # 마지막 창을 만들고, 연결 없이 정리된 스트림이 못 넣은 행을 넣는다
        return await seal_and_flush(take_id, end_reason)
