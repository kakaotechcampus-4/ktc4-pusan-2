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
  - AI 를 기다리는 동안 DB 세션을 쥐지 않는다. 동시 개수는 클라이언트가 묶는다 (3절, 4개).

실패는 던지지 않는다. 받을 사람이 없다. DB 가 잠깐 안 되면(연결 끊김 · 잠금 대기) 그 단계만 다시
하고, 그래도 안 되면 Take 는 `FINALIZING` 으로 남아 주기 작업(`sweep_forever`)이나 앱이 다시 뜰 때
이어 간다 (`resume`). 다시 해도 같은 실패(값이 칸에 맞지 않음 · 코드 오류)는 Take 결과를 만들지 못한
것과 같게 `FAILED` 로 둔다 — `FINALIZING` 으로 두면 이어 갈 때마다 AI 를 또 부르고 끝내 결과를 못
만든다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pitch_coach_backend.coach.client import CoachClient, FinalizeReply, ReplayRequired
from pitch_coach_backend.coach.requests import FinalizeSource, finalize_request
from pitch_coach_backend.coach.session import FinalizeSnapshot
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.core.retry import retry_transient
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.take.dto import StoredEvent

logger = logging.getLogger(__name__)

# DB 가 잠깐 안 되면(연결 끊김 · 잠금 대기 등) 이만큼 기다렸다 그 단계만 다시 한다. 그래도 안 되면
# FINALIZING 으로 남아 나중에 이어 간다. 받은 Take 결과는 들고 있다가 저장만 다시 한다
RETRY_DELAYS_SEC = (5.0, 30.0)
# 앱이 뜰 때 Take 결과를 기다리는 Take 를 읽는 상한. DB 가 늦어도 앱은 뜬다
RESUME_READ_TIMEOUT_SEC = 10.0
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
# DB 재시도를 다 쓴 Take 의 재료. 스트림은 이미 놓았으므로 다음 sweep 까지 여기서 보관한다.
# 저장소도 함께 둬 앱 종료 때 대기 중인 이벤트를 DB 에 한 번 더 넣어 볼 수 있게 한다.
_pending: dict[uuid.UUID, tuple[FinalizeSnapshot, CoachStore]] = {}


def spawn(
    take_id: uuid.UUID,
    snapshot: FinalizeSnapshot | None,
    *,
    client: CoachClient,
    store: CoachStore,
) -> None:
    """finalize 를 백그라운드로 띄운다. 같은 Take 가 이미 돌고 있으면 띄우지 않는다."""
    if _running.running(take_id):
        logger.info("finalize 가 이미 돌고 있다 take=%s", take_id)
        return
    _running.spawn(
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
        await _run(take_id, snapshot, client, store)
    except asyncio.CancelledError:
        # 앱을 내린다. Take 는 FINALIZING 으로 남고 다시 뜰 때 이어 간다
        logger.info("finalize 를 멈춘다 (앱 종료) take=%s", take_id)
        if snapshot is not None and snapshot.events:
            await _keep_events(take_id, snapshot.events, store)
        raise
    except Exception as error:
        if is_transient_db_error(error):
            logger.exception("finalize 가 DB 에 닿지 못했다 take=%s. 나중에 이어 간다", take_id)
            if snapshot is not None and snapshot.events:
                await _keep_events(take_id, snapshot.events, store)
            return
        logger.exception("finalize 실패 take=%s. 다시 해도 같아 Take 를 FAILED 로 둔다", take_id)
        try:
            await _retrying("실패 적기", take_id, lambda: store.fail_take_result(take_id))
        except Exception:
            logger.exception("Take 를 FAILED 로 적지 못했다 take=%s", take_id)
            return
        _pending.pop(take_id, None)
    else:
        _pending.pop(take_id, None)


async def _keep_events(
    take_id: uuid.UUID, events: tuple[StoredEvent, ...], store: CoachStore
) -> None:
    """다시 뜬 프로세스는 이 재료를 모르고 DB 의 이벤트만 읽는다. 발표 중 저장하지 못해 재료에만
    있는 코치 이벤트를 넣어 둔다 (이미 있는 것은 건너뛴다)."""
    try:
        await asyncio.wait_for(store.append_events(take_id, events), KEEP_EVENTS_TIMEOUT_SEC)
    except Exception:
        logger.exception("재개를 위해 코치 이벤트를 넣어 두지 못했다 take=%s", take_id)


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
    주면 그 전에 FINALIZING 이 된 Take 만 본다.
    """
    # 읽는 동안 새 작업이 등록될 수 있다. 이번 조회 전에 대기하던 재료만 정리 대상으로 둔다.
    pending = dict(_pending)

    async def waiting() -> tuple[list[uuid.UUID], set[uuid.UUID]]:
        take_ids = await store.finalizing_takes(stale_before)
        # 오래된 Take 만 읽은 목록에 없다고 새로 대기 중인 snapshot 까지 지우면 안 된다.
        all_waiting = (
            await store.finalizing_takes() if pending and stale_before is not None else take_ids
        )
        return take_ids, set(all_waiting)

    try:
        take_ids, all_waiting = await asyncio.wait_for(waiting(), RESUME_READ_TIMEOUT_SEC)
    except Exception:
        logger.exception("Take 결과를 기다리는 Take 를 읽지 못했다")
        return
    for take_id, entry in pending.items():
        if (
            take_id not in all_waiting
            and not _running.running(take_id)
            and _pending.get(take_id) is entry
        ):
            del _pending[take_id]
    resumed = [take_id for take_id in take_ids if not _running.running(take_id)]
    for take_id in resumed:
        spawn(take_id, None, client=client, store=store)
    if resumed:
        logger.info("Take 결과를 기다리던 Take %d개의 finalize 를 다시 띄웠다", len(resumed))


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
    await _running.stop_all(timeout)
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
