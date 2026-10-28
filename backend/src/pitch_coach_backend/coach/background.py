"""앱이 뜰 때 · 내릴 때의 코치 작업. main.py 의 lifespan 이 부른다.

  - 뜰 때: Take 결과를 기다리는 중으로 남은 Take 의 finalize 를 다시 띄우고(앞 프로세스가 만들다
    내려감), 두 주기 작업을 건다 — 기다리는 채 오래 남은 Take 를 다시 띄우기(DB 가 오래 안 돼
    재시도를 다 씀), 동의 없이 FAILED 로 끝난 Take 의 1초 원자료를 7일 뒤 지우기
  - 내릴 때: 돌고 있는 finalize 를 끊고(FINALIZING 으로 남아 다시 뜰 때 이어 간다), 계획 요청과
    주기 작업도 끊는다
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from pitch_coach_backend.coach import finalize, planner, retention
from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.store import CoachStore

# 내릴 때 finalize 가 끊기기를 기다리는 상한
STOP_TIMEOUT_SEC = 5.0


async def start(
    client: CoachClient,
    store: CoachStore,
    on_take_result: Callable[[uuid.UUID], object] | None = None,
) -> Callable[[], Awaitable[None]]:
    """작업을 띄우고, 내릴 때 부를 함수를 돌려준다."""
    finalize.on_take_result(on_take_result)
    await finalize.resume(client=client, store=store)
    loops = [
        asyncio.create_task(finalize.sweep_forever(client, store), name="coach-finalize-sweep"),
        retention.start(store),
    ]

    async def stop() -> None:
        for loop in loops:
            loop.cancel()
        await asyncio.wait(loops)
        await finalize.stop_all(STOP_TIMEOUT_SEC)
        finalize.on_take_result(None)
        await planner.stop_all(STOP_TIMEOUT_SEC)

    return stop
