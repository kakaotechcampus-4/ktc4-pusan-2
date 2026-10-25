"""Take 를 끝낸 뒤 Take 결과를 만든다 (`/coach/finalize`, 백그라운드).

    Take 종료 ─▶ (분석 단계 FINALIZING) ─▶ finalize ─409─▶ finalize + replay
                                            │
                                            └─▶ 한 트랜잭션: 이벤트 · TAKE_RESULT · 리포트 측정 칸 ·
                                                Take 길이 · 분석 단계 SCORING ·
                                                (동의 없으면) 1초 원자료 삭제

계약: `docs/ai/architecture.md` 1-3 3번 ④ · 2절 '복구',
`docs/ai/features/coach-agent.md` 5-2 · 7 · 8 · 12-2.

  - 발표 중 코치 세션이 있으면 그 `coach_state` · 마지막 창으로 부른다. 없거나(코칭이 꺼져 있었음 ·
    앱이 다시 떠 잃음) `coach_state` 가 없으면 처음부터 replay 를 붙인다.
  - 409 `REPLAY_REQUIRED` 면 DB 의 원자료로 replay 를 만들어 한 번 더 부른다. 일시 오류의 재시도는
    클라이언트가 한다. 끝내 실패하면 Take 를 `FAILED` 로 두고 원자료는 남긴다 (7일, 1-1).
  - 결과 저장은 Take 가 아직 Take 결과를 기다리는 중일 때만 한다 (`store.save_take_result`).
  - AI 를 기다리는 동안 DB 세션을 쥐지 않는다. 원자료 읽기부터 결과 저장까지 4개로 묶고,
    복구는 ID 도 4개씩 읽어 앞 묶음이 끝난 뒤 다음 묶음을 띄운다. HTTP 제한은 별도로 유지한다.

실패는 던지지 않는다. 받을 사람이 없다. DB 가 잠깐 안 되면(연결 끊김 · 잠금 대기) 그 단계만 다시
하고, 그래도 안 되면 Take 는 `FINALIZING` 으로 남아 주기 작업(`sweep_forever`)이나 앱이 다시 뜰 때
이어 간다 (`resume`). 다시 해도 같은 실패(값이 칸에 맞지 않음 · 코드 오류)는 Take 결과를 만들지 못한
것과 같게 `FAILED` 로 둔다 — `FINALIZING` 으로 두면 이어 갈 때마다 AI 를 또 부르고 끝내 결과를 못
만든다.
"""

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.replies import FinalizeReply, ReplayRequired
from pitch_coach_backend.coach.requests import FinalizeSource, finalize_request
from pitch_coach_backend.coach.session import FinalizeSnapshot
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.core.logging import sqlstate
from pitch_coach_backend.core.retry import retry_transient
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.take.dto import StoredEvent

logger = logging.getLogger(__name__)

# DB 가 잠깐 안 되면(연결 끊김 · 잠금 대기 등) 이만큼 기다렸다 그 단계만 다시 한다. 그래도 안 되면
# FINALIZING 으로 남아 나중에 이어 간다. 받은 Take 결과는 들고 있다가 저장만 다시 한다
RETRY_DELAYS_SEC = (5.0, 30.0)
# 앱이 뜰 때 Take 결과를 기다리는 Take 를 읽는 상한. DB 가 늦어도 앱은 뜬다
RESUME_READ_TIMEOUT_SEC = 10.0
# 원자료 · replay 를 메모리에 들고 있는 작업 수. HTTP 를 기다리기 전부터 묶는다
FINALIZE_CONCURRENCY = 4
RESUME_BATCH_SIZE = 4
# 앱을 내리며 finalize 를 끊을 때 재료에만 있는 코치 이벤트를 넣어 보는 상한. 끊긴 finalize 를
# 기다리는 시간(`background.STOP_TIMEOUT_SEC`) 안이다
KEEP_EVENTS_TIMEOUT_SEC = 3.0
# Take 결과를 기다리는 채 오래 남은 Take 를 다시 띄우는 간격과 기준. DB 가 오래 안 돼 재시도를 다
# 썼거나 Take 결과를 띄우지 못한 Take 가 앱이 다시 뜰 때까지 기다리지 않게 한다. 막 끝낸 Take 는
# 그 Take 의 finalize 가 곧 뜨므로 건드리지 않는다
SWEEP_INTERVAL_SEC = 5 * 60.0
STALE_FINALIZING = timedelta(minutes=10)

# 돌고 있는 finalize. Take id 로 찾아 같은 Take 를 두 번 띄우지 않는다
_running = TaskRegistry()
# 앱 시작 · 주기 복구가 겹쳐 같은 backlog 를 동시에 훑지 않게 한다
_recovery = TaskRegistry()
# 테스트의 여러 이벤트 루프에서도 각각 같은 상한을 쓴다 (client 의 HTTP 제한과 같은 방식)
_slots_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
# DB 재시도를 다 쓴 Take 의 재료. 스트림은 이미 놓았으므로 다음 sweep 까지 여기서 보관한다.
# 저장소도 함께 둬 앱 종료 때 대기 중인 이벤트를 DB 에 한 번 더 넣어 볼 수 있게 한다.
_pending: dict[uuid.UUID, tuple[FinalizeSnapshot, CoachStore]] = {}
_on_take_result: Callable[[uuid.UUID], object] | None = None


def on_take_result(callback: Callable[[uuid.UUID], object] | None) -> None:
    global _on_take_result
    _on_take_result = callback


def spawn(
    take_id: uuid.UUID,
    snapshot: FinalizeSnapshot | None,
    *,
    client: CoachClient,
    store: CoachStore,
) -> asyncio.Task[None] | None:
    """finalize 를 백그라운드로 띄운다. 같은 Take 가 이미 돌고 있으면 띄우지 않는다."""
    if _running.running(take_id):
        logger.info("finalize 가 이미 돌고 있다 take=%s", take_id)
        return
    return _running.spawn(
        run(take_id, snapshot, client=client, store=store),
        name=f"coach-finalize:{take_id}",
        key=take_id,
    )


async def run(
    take_id: uuid.UUID,
    snapshot: FinalizeSnapshot | None,
    *,
    client: CoachClient,
    store: CoachStore,
) -> None:
    if snapshot is None and take_id in _pending:
        snapshot = _pending[take_id][0]
    if snapshot is not None:
        _pending[take_id] = (snapshot, store)
    try:
        async with _slots():
            await _run(take_id, snapshot, client, store)
    except asyncio.CancelledError:
        # 앱을 내린다. Take 는 FINALIZING 으로 남고 다시 뜰 때 이어 간다
        logger.info("finalize 를 멈춘다 (앱 종료) take=%s", take_id)
        if snapshot is not None and snapshot.events:
            await _keep_events(take_id, snapshot.events, store)
        raise
    except Exception as error:
        if is_transient_db_error(error):
            logger.error(
                "finalize 가 DB 에 닿지 못했다 take=%s. 나중에 이어 간다 error=%s sqlstate=%s",
                take_id,
                type(error).__name__,
                sqlstate(error),
            )
            if snapshot is not None and snapshot.events:
                await _keep_events(take_id, snapshot.events, store)
            return
        logger.error(
            "finalize 실패 take=%s. 다시 해도 같아 Take 를 FAILED 로 둔다 error=%s sqlstate=%s",
            take_id,
            type(error).__name__,
            sqlstate(error),
        )
        try:
            await _retrying("실패 적기", take_id, lambda: store.fail_take_result(take_id))
        except Exception as mark_error:
            logger.error(
                "Take 를 FAILED 로 적지 못했다 take=%s error=%s sqlstate=%s",
                take_id,
                type(mark_error).__name__,
                sqlstate(mark_error),
            )
            return
        _pending.pop(take_id, None)
    else:
        _pending.pop(take_id, None)


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (slots := _slots_by_loop.get(loop)) is None:
        slots = asyncio.Semaphore(FINALIZE_CONCURRENCY)
        _slots_by_loop[loop] = slots
    return slots


async def _keep_events(
    take_id: uuid.UUID, events: tuple[StoredEvent, ...], store: CoachStore
) -> None:
    """다시 뜬 프로세스는 이 재료를 모르고 DB 의 이벤트만 읽는다. 발표 중 저장하지 못해 재료에만
    있는 코치 이벤트를 넣어 둔다 (이미 있는 것은 건너뛴다)."""
    try:
        await asyncio.wait_for(store.append_events(take_id, events), KEEP_EVENTS_TIMEOUT_SEC)
    except Exception as error:
        logger.error(
            "재개를 위해 코치 이벤트를 넣어 두지 못했다 take=%s error=%s sqlstate=%s",
            take_id,
            type(error).__name__,
            sqlstate(error),
        )


async def _retrying[T](what: str, take_id: uuid.UUID, step: Callable[[], Awaitable[T]]) -> T:
    """DB 단계 하나를 DB 가 잠깐 안 될 때만 `RETRY_DELAYS_SEC` 간격으로 다시 한다."""
    return await retry_transient(step, RETRY_DELAYS_SEC, what=f"finalize {what} take={take_id}")


async def _run(
    take_id: uuid.UUID,
    snapshot: FinalizeSnapshot | None,
    client: CoachClient,
    store: CoachStore,
) -> None:
    source: FinalizeSource | None = None
    live = (
        snapshot is not None and snapshot.constants is not None and snapshot.coach_state is not None
    )
    if live:
        assert snapshot is not None and snapshot.constants is not None
        t_end_ms = snapshot.t_ms
        saved_events = await _retrying("이벤트 읽기", take_id, lambda: store.load_events(take_id))
        if saved_events is None:
            logger.info("Take 결과를 기다리는 Take 가 아니다 (지웠거나 이미 끝남) take=%s", take_id)
            return
        events = _merged(saved_events, snapshot.events)
        request = finalize_request(
            snapshot.constants, t_end_ms, snapshot.inputs, snapshot.coach_state, events
        )
    else:
        source = await _retrying(
            "원자료 읽기", take_id, lambda: store.load_finalize_source(take_id)
        )
        if source is None:
            logger.info("Take 결과를 기다리는 Take 가 아니다 (지웠거나 이미 끝남) take=%s", take_id)
            return
        t_end_ms = snapshot.t_ms if snapshot is not None else source.t_end_ms
        if t_end_ms is None:
            # 발표 중 1초 원자료가 하나도 오지 않았다 (실시간 연결 없이 끝낸 Take). 셀 것이 없다
            logger.warning("1초 원자료가 없어 Take 결과를 만들 수 없다 take=%s", take_id)
            await _retrying("실패 적기", take_id, lambda: store.fail_take_result(take_id))
            return
        events = _merged(source.events, snapshot.events if snapshot else ())
        constants = (
            snapshot.constants
            if snapshot is not None and snapshot.constants is not None
            else source.context.constants
        )
        replay = source.replay(t_end_ms)
        inputs = snapshot.inputs if snapshot is not None else source.last_window(replay, t_end_ms)
        request = finalize_request(constants, t_end_ms, inputs, None, events, replay=replay)

    reply = await client.finalize(request)
    if isinstance(reply, ReplayRequired):
        if source is None:
            source = await _retrying(
                "원자료 읽기", take_id, lambda: store.load_finalize_source(take_id)
            )
            if source is None:
                logger.info("replay 를 만들 Take 가 없다 (지워졌거나 이미 끝남) take=%s", take_id)
                return
        logger.info("finalize 가 replay 를 요청했다 take=%s", take_id)
        replay = request.get("replay") or source.replay(t_end_ms)
        reply = await client.finalize({**request, "replay": replay})
        if isinstance(reply, ReplayRequired):
            # 원자료를 다 보냈는데도 못 셌다 (클라이언트가 None 으로 바꾸지만 한 번 더 막는다)
            reply = None

    if reply is None:
        logger.warning("Take 결과를 만들지 못해 Take 를 FAILED 로 둔다 take=%s", take_id)
        await _retrying("실패 적기", take_id, lambda: store.fail_take_result(take_id))
        return
    assert isinstance(reply, FinalizeReply)
    result = reply
    unsaved = snapshot.events if snapshot is not None else ()
    saved = await _retrying(
        "결과 저장", take_id, lambda: store.save_take_result(take_id, result, unsaved)
    )
    if not saved:
        logger.info("Take 결과를 버렸다 (그 사이 지워졌거나 단계가 바뀜) take=%s", take_id)
        return
    logger.info(
        "Take 결과를 저장했다 take=%s duration_ms=%d replayed=%s",
        take_id,
        reply.take_result["duration_ms"],
        reply.take_result.get("replayed"),
    )
    if _on_take_result is not None:
        try:
            _on_take_result(take_id)
        except Exception:
            logger.exception("Take 결과 뒤 분석을 띄우지 못했다 take=%s", take_id)


def _merged(
    saved: Iterable[Mapping[str, Any]], session: Iterable[StoredEvent]
) -> list[dict[str, Any]]:
    """저장된 이벤트 뒤에 세션이 들고 있던 것 중 아직 없는 것을 받은 순서대로 붙인다.

    앱이 다시 떠 세션이 Take 중간부터 받았으면 앞부분은 DB 에만 있고, 저장이 실패한 뒷부분은
    세션에만 있다. 같은 `event_id` 는 같은 이벤트다 (coach-agent.md 5-3).
    """
    merged = [dict(event) for event in saved]
    seen = {event.get("event_id") for event in merged}
    for event, _meta in session:
        if event.get("event_id") not in seen:
            merged.append(dict(event))
            seen.add(event.get("event_id"))
    return merged


async def resume(
    *, client: CoachClient, store: CoachStore, stale_before: datetime | None = None
) -> None:
    """Take 결과를 기다리는 중으로 남은 Take 를 replay 로 이어 간다. 앱이 뜰 때와 주기 작업
    (`sweep_forever`)이 부른다. 이 프로세스에서 이미 돌고 있는 Take 는 건너뛴다 (`spawn`).

    이 프로세스에서 보관 중인 재료가 있으면 이어 쓰고, 앞 프로세스의 세션을 잃었으면 처음부터
    replay 를 붙인다 (architecture.md 1-3 'BE가 재시작할 때' `FINALIZING`). `stale_before` 를
    주면 그 전에 FINALIZING 이 된 Take 만 본다. 조회 · 복구는 별도 태스크에서 배치로 진행해
    앱 시작을 막지 않고, 이미 복구 중이면 새 복구를 띄우지 않는다.
    """
    if not _recovery.running("resume"):
        _recovery.spawn(
            _resume(client, store, stale_before), name="coach-finalize-resume", key="resume"
        )


async def _waiting_batches(
    store: CoachStore, stale_before: datetime | None
) -> AsyncIterator[list[uuid.UUID]]:
    after = None
    while True:
        # 각 배치의 DB 읽기에만 상한을 건다. 복구 전체나 AI 를 기다리는 시간은 묶지 않는다.
        take_ids = await asyncio.wait_for(
            store.finalizing_takes(stale_before, after=after, limit=RESUME_BATCH_SIZE),
            RESUME_READ_TIMEOUT_SEC,
        )
        if not take_ids:
            return
        yield take_ids
        if len(take_ids) < RESUME_BATCH_SIZE:
            return
        after = take_ids[-1]


async def _resume(client: CoachClient, store: CoachStore, stale_before: datetime | None) -> None:
    # 읽는 동안 새 작업이 등록될 수 있다. 이번 조회 전에 대기하던 재료만 정리 대상으로 둔다.
    pending = dict(_pending)
    resumed = 0
    try:
        if pending and stale_before is not None:
            # 오래된 Take 조회에 빠진 새 snapshot 도 전체 대기 목록에 있으면 보관한다.
            async for take_ids in _waiting_batches(store, None):
                for take_id in take_ids:
                    pending.pop(take_id, None)
        async for take_ids in _waiting_batches(store, stale_before):
            tasks = []
            for take_id in take_ids:
                if stale_before is None:
                    pending.pop(take_id, None)
                task = spawn(take_id, None, client=client, store=store)
                if task is not None:
                    tasks.append(task)
            resumed += len(tasks)
            # 결과 저장까지 끝낸 뒤 다음 ID 를 읽어 태스크 수도 한 배치로 묶는다.
            if tasks:
                await asyncio.gather(*tasks)
    except Exception as error:
        # 조회가 덜 끝났으면 대기 재료를 지우지 않고 다음 sweep 에서 이어 간다.
        logger.error(
            "Take 결과를 기다리는 Take 를 읽지 못했다 error=%s sqlstate=%s",
            type(error).__name__,
            sqlstate(error),
        )
        return
    for take_id, entry in pending.items():
        if not _running.running(take_id) and _pending.get(take_id) is entry:
            del _pending[take_id]
    if resumed:
        logger.info("Take 결과를 기다리던 Take %d개의 finalize 를 다시 띄웠다", resumed)


async def sweep_forever(
    client: CoachClient, store: CoachStore, interval_sec: float = SWEEP_INTERVAL_SEC
) -> None:
    """Take 결과를 기다리는 채 오래 남은 Take 를 주기적으로 다시 띄운다."""
    while True:
        await asyncio.sleep(interval_sec)
        await resume(client=client, store=store, stale_before=datetime.now(UTC) - STALE_FINALIZING)


async def stop_all(timeout: float) -> None:
    """앱 종료. 돌고 있는 finalize 를 끊는다 — FINALIZING 으로 남아 다시 뜰 때 이어 간다."""
    deadline = asyncio.get_running_loop().time() + timeout
    await _recovery.stop_all(timeout)
    await _running.stop_all(max(0.0, deadline - asyncio.get_running_loop().time()))
    # 재시도 소진 뒤 sweep 을 기다리던 Take 는 실행 태스크가 없어 위 취소 경로를 타지 않는다.
    if pending := [
        _keep_events(take_id, snapshot.events, store)
        for take_id, (snapshot, store) in list(_pending.items())
        if snapshot.events
    ]:
        try:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait_for(asyncio.gather(*pending), remaining)
        except TimeoutError:
            logger.warning("대기 중인 finalize 이벤트를 모두 저장하기 전에 종료 시간이 지났다")
