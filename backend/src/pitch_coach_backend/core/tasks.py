"""백그라운드 태스크를 들고 있다가 앱을 내릴 때 끊는다 (finalize · 코칭 계획 · Take 끝내기 정리)."""

import asyncio
import logging
from collections.abc import Coroutine, Hashable
from typing import Any

logger = logging.getLogger(__name__)


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
