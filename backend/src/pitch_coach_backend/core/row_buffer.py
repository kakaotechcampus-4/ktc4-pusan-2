"""요청 밖에서 행을 모았다가 한 번에 저장하는 버퍼 (발표 중 1초 원자료 · 장 변경 · 코치 이벤트).

`batch` 개가 모이면 백그라운드로 넣는다. DB 가 잠깐 안 돼 실패한 행은 들고 있다가 `RETRY_DELAYS_SEC`
간격으로 다시 넣는다. 다시 해도 같은 실패(값이 칸에 맞지 않음 등)면 그 묶음은 버린다 — 들고 있으면
뒤에 온 행까지 그 뒤에 막힌다. 넣을 곳이 지워졌으면(`gone_errors`) 더 받지 않는다.

`seal` 뒤로 오는 행은 받지 않는다. `close` 는 마지막 `flush` 뒤에 불러 못 넣은 행을 버리고 다시
넣기를 멈춘다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.core.logging import sqlstate

logger = logging.getLogger(__name__)

# 저장에 실패한 뒤 다시 넣기까지 기다리는 시간(초). 이 뒤로는 마지막 값을 반복한다. 그동안 온 행은
# 모아 두기만 한다 — 실패한 채로 1초마다 다시 넣으면 DB 가 죽어 있는 동안 버퍼 수만큼 매초 커넥션을
# 빌리고 스택트레이스를 남긴다
RETRY_DELAYS_SEC = (1.0, 2.0, 5.0, 10.0, 30.0)


class RowBuffer[T]:
    """`owner_id`(행이 붙은 것, 예: Take)의 행을 모았다가 `save(owner_id, rows)` 로 넣는다. `what`
    은 로그에 남길 이름, `batch` 개가 모이면 백그라운드로 넣는다. `key` 는 로그에 남길 행의
    시각이다. 저장하지 못해 들고 있을 행은 `max_unsaved` 개까지이고, 넘치면 오래된 것부터 버린다."""

    def __init__(
        self,
        owner_id: uuid.UUID,
        save: Callable[[uuid.UUID, list[T]], Awaitable[None]],
        *,
        what: str,
        batch: int,
        key: Callable[[T], float],
        max_unsaved: int,
        gone_errors: tuple[type[Exception], ...] = (),
    ) -> None:
        self.owner_id = owner_id
        self._save = save
        self._what = what
        self._batch = batch
        self._key = key
        self._max_unsaved = max_unsaved
        self._gone_errors = gone_errors
        self._rows: list[T] = []
        # 마지막 저장까지 했다 (`close`). 이 뒤로는 되돌려 넣지도 다시 넣지도 않는다
        self._closed = False
        # 저장을 하나씩만 돌린다. 끝낼 때의 flush 가 돌고 있던 저장이 끝나기를 기다린다
        self._lock = asyncio.Lock()
        # 백그라운드 저장. asyncio 는 태스크를 약하게만 잡고 있어서 참조를 들고 있어야 한다
        self._task: asyncio.Task[bool] | None = None
        # 넣을 곳이 지워졌다. 더 받지 않는다
        self._gone = False
        # 넣을 곳이 지워진 걸 알았을 때 부른다. 쓰는 쪽이 붙였다 뗀다 (발표 중 스트림이 끊는다)
        self.on_gone: Callable[[], None] | None = None
        # 끝냈다 (`seal`). 더 받지 않는다
        self._sealed = False
        # 이어서 실패한 횟수와 다시 넣을 타이머. 타이머가 걸려 있는 동안은 백그라운드로 넣지 않는다
        self._failures = 0
        self._retry: asyncio.TimerHandle | None = None

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def gone(self) -> bool:
        """넣을 곳이 지워졌다 (`gone_errors`). 더 받지 않는다."""
        return self._gone

    @property
    def pending(self) -> bool:
        """저장할 행이 남았거나 저장이 돌고 있다. 돌고 있는 저장은 행을 꺼내 가 `len` 이 0 이다."""
        return bool(self._rows) or (self._task is not None and not self._task.done())

    def add(self, row: T) -> None:
        if self._gone:
            return
        if self._sealed:
            # 끝낸 뒤에 왔다 (Take 를 끝냄). 뒤 분석이 이미 원자료를 읽기 시작했다
            logger.info(
                "닫은 뒤에 온 %s 를 버린다 id=%s t_ms=%s",
                self._what,
                self.owner_id,
                self._key(row),
            )
            return
        self._rows.append(row)
        self._trim()
        if len(self._rows) >= self._batch:
            self.flush_soon()

    def seal(self) -> None:
        """끝낸다. 이 뒤로 오는 행은 받지 않는다. 들고 있는 행은 `flush` 로 넣는다."""
        self._sealed = True

    def close(self) -> int:
        """마지막 `flush` 뒤에 부른다. 다시 넣을 타이머를 끄고 못 넣은 행을 버린다. 버린 개수를
        돌려준다.

        늦게 넣으면 안 될 때 쓴다 — 동의 없는 Take 는 Take 결과를 저장할 때 1초 원자료를 지우는데,
        그 뒤에 들어간 행은 끝내 남는다.
        """
        self._sealed = True
        self._closed = True
        self._cancel_retry()
        dropped = len(self._rows)
        self._rows.clear()
        return dropped

    def flush_soon(self) -> None:
        """백그라운드로 저장한다. 이미 저장 중이면 그 저장이 새로 온 행까지 넣는다.

        저장에 실패해 다시 넣을 때를 기다리는 중이면 그 타이머에 맡긴다.
        """
        if not self._rows or self._retry is not None:
            return
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 밖이다 (앱이 내려간 뒤의 정리). 앱 종료 때 넣는 일은 쓰는 쪽이 한다
            return
        self._task = loop.create_task(self.flush(), name=f"save-rows:{self.owner_id}")

    async def flush(self) -> bool:
        """들고 있는 행을 모두 저장한다. 다 넣었으면 True.

        DB 가 잠깐 안 되면 그 행을 되돌려 두고 멈춘다 — `RETRY_DELAYS_SEC` 뒤나 끝낼 때 다시 넣는다.
        다시 해도 같은 실패면 그 묶음을 버리고 다음 행으로 간다. 취소되면(상한을 넘긴 기다림)
        되돌려 둔 채로 취소를 그대로 올린다.
        """
        complete = True
        async with self._lock:
            while self._rows:
                rows, self._rows = self._rows, []
                try:
                    await self._save(self.owner_id, rows)
                except self._gone_errors:
                    # 넣을 곳이 지워졌다 (Take 를 지움)
                    logger.warning(
                        "넣을 곳이 지워져 %s 를 버린다 id=%s count=%d",
                        self._what,
                        self.owner_id,
                        len(rows),
                    )
                    self._gone = True
                    self._rows.clear()
                    self._cancel_retry()
                    if self.on_gone is not None:
                        self.on_gone()
                    return False
                except BaseException as e:
                    if self._closed:
                        # 닫은 뒤 돌던 저장이 실패했다. 되돌려 넣으면 다시 넣을 타이머가 또 걸린다
                        if not isinstance(e, Exception):
                            raise
                        logger.error(
                            "닫은 뒤 %s 저장이 실패해 버린다 id=%s count=%d",
                            self._what,
                            self.owner_id,
                            len(rows),
                        )
                        return False
                    if isinstance(e, Exception) and not is_transient_db_error(e):
                        # 다시 넣어도 같다. 들고 있으면 뒤에 온 행까지 막히므로 이 묶음만 버린다
                        logger.error(
                            "%s 를 저장할 수 없어 버린다 id=%s count=%d t_ms=%s~%s "
                            "error=%s sqlstate=%s",
                            self._what,
                            self.owner_id,
                            len(rows),
                            self._key(rows[0]),
                            self._key(rows[-1]),
                            type(e).__name__,
                            sqlstate(e),
                        )
                        complete = False
                        continue
                    self._rows = rows + self._rows
                    self._trim()
                    if not isinstance(e, Exception):
                        raise
                    self._failures += 1
                    delay = RETRY_DELAYS_SEC[min(self._failures, len(RETRY_DELAYS_SEC)) - 1]
                    logger.error(
                        "%s 저장 실패 id=%s 남은 개수=%d %.0f초 뒤 다시 넣는다 "
                        "error=%s sqlstate=%s",
                        self._what,
                        self.owner_id,
                        len(self._rows),
                        delay,
                        type(e).__name__,
                        sqlstate(e),
                    )
                    self._schedule_retry(delay)
                    return False
            self._failures = 0
            self._cancel_retry()
            return complete

    def _schedule_retry(self, delay: float) -> None:
        self._cancel_retry()
        self._retry = asyncio.get_running_loop().call_later(delay, self._on_retry)

    def _on_retry(self) -> None:
        self._retry = None
        self.flush_soon()

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    def _trim(self) -> None:
        overflow = len(self._rows) - self._max_unsaved
        if overflow <= 0:
            return
        dropped = self._rows[:overflow]
        del self._rows[:overflow]
        logger.error(
            "저장 못 한 %s 가 너무 많아 오래된 것을 버린다 id=%s t_ms=%s~%s",
            self._what,
            self.owner_id,
            self._key(dropped[0]),
            self._key(dropped[-1]),
        )
