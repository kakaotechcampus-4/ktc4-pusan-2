"""백그라운드 태스크 부품. 들고 있다가 앱을 내릴 때 끊고, 주기 작업을 띄운다."""

import asyncio
import contextlib
import logging
import weakref
from collections.abc import Awaitable, Callable, Coroutine, Hashable
from typing import Any

logger = logging.getLogger(__name__)

type Stop = Callable[[], Awaitable[None]]
type SemaphoreByLoop = weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]


def being_cancelled() -> bool:
    """지금 태스크가 취소되는 중인가. 기다리던 다른 태스크가 끊겨 난 CancelledError 와 가른다."""
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


async def quiet(awaitable: Awaitable[object]) -> None:
    """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다.

    정리 경로라서 넓게 잡는다 — 여기서 예외가 새면 그다음 정리가 통째로 멈춘다.
    """
    with contextlib.suppress(Exception):
        await awaitable


def loop_semaphore(by_loop: SemaphoreByLoop, limit: int) -> asyncio.Semaphore:
    """이벤트 루프마다 하나인 세마포어. asyncio.Semaphore 는 처음 기다린 루프에 묶여서, 하나를 여러
    루프가 쓰면(시험의 TestClient 마다 루프가 다르다) 다른 루프에서 기다리는 순간 RuntimeError 다.
    운영은 uvicorn 루프 하나라 프로세스 전체에 하나다."""
    loop = asyncio.get_running_loop()
    if (slots := by_loop.get(loop)) is None:
        slots = asyncio.Semaphore(limit)
        by_loop[loop] = slots
    return slots


async def run_periodic(
    step: Callable[[], Awaitable[object]], interval_sec: float, *, delay_first: bool = False
) -> None:
    """step 을 주기마다 부른다. step 의 예외는 step 이 잡아 다음 주기로 넘긴다."""
    if delay_first:
        await asyncio.sleep(interval_sec)
    while True:
        await step()
        await asyncio.sleep(interval_sec)


def start_task(coro: Coroutine[Any, Any, None], *, name: str) -> Stop:
    """태스크를 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    task = asyncio.create_task(coro, name=name)

    async def stop() -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # 뒤따르는 종료 절차가 멈추지 않게 남기고 넘어간다
            logger.exception("주기 작업이 실패해 멈춰 있었다 %s", name)

    return stop


def start_periodic(
    step: Callable[[], Awaitable[object]],
    interval_sec: float,
    *,
    name: str,
    delay_first: bool = False,
) -> Stop:
    """주기 작업을 띄우고, 앱을 내릴 때 부를 함수를 돌려준다."""
    return start_task(run_periodic(step, interval_sec, delay_first=delay_first), name=name)


class TaskRegistry:
    """돌고 있는 백그라운드 태스크. asyncio 는 태스크를 약하게만 잡아서 참조를 들고 있어야 끝나기
    전에 GC 되지 않는다. 끝나면 스스로 빠진다."""

    def __init__(self) -> None:
        self._tasks: dict[Hashable, asyncio.Task[None]] = {}

    def __len__(self) -> int:
        return len(self._tasks)

    def tasks(self) -> list[asyncio.Task[None]]:
        return list(self._tasks.values())

    def running(self, key: Hashable) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    def spawn(
        self, coro: Coroutine[Any, Any, None], *, name: str, key: Hashable | None = None
    ) -> asyncio.Task[None]:
        """태스크를 띄워 들고 있는다. `key` 를 주면 그 키로 찾는다 — 같은 일을 두 번 띄우지 않을
        때 쓴다."""
        task = asyncio.create_task(coro, name=name)
        slot = task if key is None else key
        self._tasks[slot] = task
        task.add_done_callback(lambda done: self._done(slot, done))
        return task

    def _done(self, key: Hashable, task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        # 아무도 await 하지 않는 태스크라 예외를 여기서 꺼내 남긴다. 안 그러면 GC 될 때
        # "Task exception was never retrieved" 로만 찍히고 어느 작업인지 알 수 없다
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("백그라운드 태스크 실패 %s", task.get_name(), exc_info=error)

    async def stop_all(self, timeout: float) -> None:
        """앱 종료. 돌고 있는 것을 끊고 끝나기를 `timeout` 까지 기다린다."""
        tasks = self.tasks()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
