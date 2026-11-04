"""떠난 Take 를 끝낼 타이머. 끝나지 않은 채 스트림이 정리되고 아무도 다시 붙지 않으면 끝낸다.

기다리는 시간과 이유는 끊긴 방식으로 정한다. 창을 닫았으면(닫힘 코드 1001) `CLOSED_TAB_WAIT_SEC`
뒤에 `CLOSED`, 그 밖은 `ABANDON_AFTER_SEC` 뒤에 `ABANDONED` 다 (docs/be/take-lifecycle.md).

여기서는 타이머를 걸고 끄기만 한다. 만료되면 받은 콜백(`on_expire`)을 부르고, 그 사이 끝났거나 다시
붙었는지 보고 DB 에 적는 일은 registry 가 한다. registry 를 import 하지 않는다.
타이머는 메모리에만 있어 앱이 다시 뜨면 DB 를 보고 다시 건다 (`arm_running_takes`).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC
from pitch_coach_backend.realtime.storage.store import TranscriptStore

logger = logging.getLogger(__name__)

# 창을 닫아(닫힘 코드 1001) 끊긴 스트림이 정리된 뒤 기다리는 시간. 새로고침도 1001 로 끊기므로
# 다시 붙을 틈을 둔다 — 카메라 · 시선 모델을 다시 띄우느라 grace(30초)를 넘길 수 있다
CLOSED_TAB_WAIT_SEC = 2 * 60.0
# 앱이 뜰 때 진행 중인 Take 를 읽는 상한. 이걸 기다리는 동안 앱이 요청을 받지 못한다
ARM_ABANDONED_TIMEOUT_SEC = 10.0

# 떠난 것으로 끝낸 Take 의 `end_reason`. 창을 닫았으면 CLOSED, 그 밖은 ABANDONED 다 —
# ABANDONED 는 FE 가 STT 없이 발표를 이어 가는 중일 수 있어 끝내기 PUT 이 오면 되살린다
# (take repository `mark_ended`)
type LeftReason = Literal["ABANDONED", "CLOSED"]

# 타이머가 만료되면 부른다. (Take, 마지막으로 연결이 끊긴 시각, 이유)
type OnExpire = Callable[[uuid.UUID, datetime, LeftReason], None]

# 떠난 Take 를 끝낼 타이머. 스트림이 새로 열리면 끈다 (`TakeStream.start`)
_abandon_timers: dict[uuid.UUID, asyncio.TimerHandle] = {}


def arm(
    take_id: uuid.UUID,
    *,
    closed_at: datetime,
    on_expire: OnExpire,
    delay_sec: float | None = None,
    end_reason: LeftReason = "ABANDONED",
) -> None:
    """`delay_sec`(기본 `ABANDON_AFTER_SEC`) 뒤에 `on_expire` 를 부른다. 걸려 있으면 다시 건다.

    `closed_at` 은 마지막으로 연결이 끊긴 시각이다. Take 의 끝난 시각으로 적는다 — 기다린 시간까지
    연습한 시간에 넣지 않는다. `end_reason` 은 창을 닫았으면 `CLOSED`, 그 밖은 `ABANDONED` 다.
    """
    cancel(take_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 이벤트 루프 밖이다 (앱이 내려간 뒤의 정리). 다시 뜰 때 DB 를 보고 다시 건다
        return
    delay = ABANDON_AFTER_SEC if delay_sec is None else delay_sec
    _abandon_timers[take_id] = loop.call_later(
        max(0.0, delay), _expire, take_id, closed_at, end_reason, on_expire
    )


def armed(take_id: uuid.UUID) -> bool:
    return take_id in _abandon_timers


def cancel(take_id: uuid.UUID) -> None:
    timer = _abandon_timers.pop(take_id, None)
    if timer is not None:
        timer.cancel()


def _expire(
    take_id: uuid.UUID, closed_at: datetime, end_reason: LeftReason, on_expire: OnExpire
) -> None:
    _abandon_timers.pop(take_id, None)
    on_expire(take_id, closed_at, end_reason)


async def arm_running_takes(store: TranscriptStore, *, on_expire: OnExpire) -> None:
    """앱이 뜰 때 부른다. 진행 중으로 남은 Take 마다 떠난 것으로 끝낼 타이머를 다시 건다.

    앞 프로세스의 타이머와 끊긴 시각은 사라졌다. 그래서 마지막으로 활동한 시각(전사 저장 등)에서
    `ABANDON_AFTER_SEC` 뒤로 건다 — 닫힘 코드도 사라져 창을 닫았는지 모르므로 이유는 `ABANDONED` 다.
    이미 지났으면 바로 끝낸다. 그 안에 FE 가 다시 붙으면 꺼진다.
    """
    try:
        takes = await asyncio.wait_for(store.running_takes(), ARM_ABANDONED_TIMEOUT_SEC)
    except Exception:
        # 앱은 뜬다 (상한을 넘겨도). 이 Take 들은 다음에 연결이 붙었다 끊길 때 다시 걸린다
        logger.exception("진행 중인 Take 를 읽지 못해 떠난 Take 타이머를 걸지 못했다")
        return
    now = datetime.now(UTC)
    for take_id, last_active_at in takes:
        idle_sec = (now - last_active_at).total_seconds()
        arm(
            take_id,
            closed_at=last_active_at,
            on_expire=on_expire,
            delay_sec=ABANDON_AFTER_SEC - idle_sec,
        )
    if takes:
        logger.info("진행 중으로 남은 Take %d개에 떠난 Take 타이머를 걸었다", len(takes))
