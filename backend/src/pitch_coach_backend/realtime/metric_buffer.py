"""발표 중 1초 원자료(WS `metrics`)를 모았다가 몇 초씩 한 번에 저장한다.

1초마다 DB 를 왕복하면 Take 하나가 한 시간에 3,600번 커넥션을 빌린다. 그래서 `METRICS_BATCH`
개가 모이면 백그라운드로 한 번에 넣는다 (`docs/ai/architecture.md` 1-3 "몇 초씩 모아 저장한다").

버퍼는 스트림이 아니라 Take 에 붙어 있다 (`take_stream._TakeRecord`). grace 가 지나 스트림이 새로
열려도 앞 스트림이 못 넣은 행이 이어진다.

Take 를 끝낼 때(`ANALYZING`)는 그 전에 `seal` 하고 `flush` 를 기다린다. Take 뒤 분석이 원자료를
읽으므로 그때 다 있어야 하고, 그 뒤에 온 행은 받지 않는다. 저장에 실패한 행은 들고 있다가
`RETRY_DELAYS_SEC` 간격으로 다시 넣는다.
"""

import asyncio
import logging
import uuid

from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.realtime.transcript_store import TranscriptStore

logger = logging.getLogger(__name__)

# 이만큼 모이면 저장한다. FE 는 1초에 하나를 보내므로 5초마다다
METRICS_BATCH = 5
# 저장하지 못해 들고 있을 행 수. 넘치면 오래된 것부터 버린다. 1초에 하나라 10분이다
MAX_UNSAVED_METRICS = 600
# 저장에 실패한 뒤 다시 넣기까지 기다리는 시간(초). 이 뒤로는 마지막 값을 반복한다. 그동안 온 행은
# 모아 두기만 한다 — 실패한 채로 1초마다 다시 넣으면 DB 가 죽어 있는 동안 Take 수만큼 매초 커넥션을
# 빌리고 스택트레이스를 남긴다
RETRY_DELAYS_SEC = (1.0, 2.0, 5.0, 10.0, 30.0)


class MetricBuffer:
    def __init__(self, take_id: uuid.UUID, store: TranscriptStore) -> None:
        self.take_id = take_id
        self._store = store
        self._rows: list[MetricSecondCreateDTO] = []
        # 저장을 하나씩만 돌린다. Take 를 끝낼 때의 flush 가 돌고 있던 저장이 끝나기를 기다린다
        self._lock = asyncio.Lock()
        # 백그라운드 저장. asyncio 는 태스크를 약하게만 잡고 있어서 참조를 들고 있어야 한다
        self._task: asyncio.Task[bool] | None = None
        # Take 가 지워졌다. 더 받지 않는다
        self._gone = False
        # Take 를 끝냈다 (`seal`). 더 받지 않는다
        self._sealed = False
        # 이어서 실패한 횟수와 다시 넣을 타이머. 타이머가 걸려 있는 동안은 백그라운드로 넣지 않는다
        self._failures = 0
        self._retry: asyncio.TimerHandle | None = None

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def pending(self) -> bool:
        """저장할 행이 남았거나 저장이 돌고 있다. 돌고 있는 저장은 행을 꺼내 가 `len` 이 0 이다."""
        return bool(self._rows) or (self._task is not None and not self._task.done())

    def add(self, row: MetricSecondCreateDTO) -> None:
        if self._gone:
            return
        if self._sealed:
            # Take 를 끝낸 뒤에 왔다. 분석이 이미 원자료를 읽기 시작했다
            logger.info("끝낸 Take 의 1초 원자료를 버린다 take=%s t_ms=%d", self.take_id, row.t_ms)
            return
        self._rows.append(row)
        self._trim()
        if len(self._rows) >= METRICS_BATCH:
            self.flush_soon()

    def seal(self) -> None:
        """Take 를 끝낸다. 이 뒤로 오는 행은 받지 않는다. 들고 있는 행은 `flush` 로 넣는다."""
        self._sealed = True

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
            # 이벤트 루프 밖이다 (앱이 내려간 뒤의 정리). 앱 종료는 `take_stream.stop_all` 이 넣는다
            return
        self._task = loop.create_task(self.flush(), name=f"take-metrics:{self.take_id}")

    async def flush(self) -> bool:
        """들고 있는 행을 모두 저장한다. 다 넣었으면 True.

        실패하면 그 행을 되돌려 두고 멈춘다 — `RETRY_DELAYS_SEC` 뒤나 Take 를 끝낼 때 다시 넣는다.
        취소되면(상한을 넘긴 기다림) 되돌려 둔 채로 취소를 그대로 올린다.
        """
        async with self._lock:
            while self._rows:
                rows, self._rows = self._rows, []
                try:
                    await self._store.append_metrics(self.take_id, rows)
                except NonExistentTake:
                    # Take 가 지워졌다. 저장할 곳이 없다
                    logger.warning(
                        "Take 가 지워져 1초 원자료를 버린다 take=%s count=%d",
                        self.take_id,
                        len(rows),
                    )
                    self._gone = True
                    self._rows.clear()
                    self._cancel_retry()
                    return False
                except BaseException as e:
                    self._rows = rows + self._rows
                    self._trim()
                    if not isinstance(e, Exception):
                        raise
                    self._failures += 1
                    delay = RETRY_DELAYS_SEC[min(self._failures, len(RETRY_DELAYS_SEC)) - 1]
                    logger.exception(
                        "1초 원자료 저장 실패 take=%s 남은 개수=%d %.0f초 뒤 다시 넣는다",
                        self.take_id,
                        len(self._rows),
                        delay,
                    )
                    self._schedule_retry(delay)
                    return False
            self._failures = 0
            self._cancel_retry()
            return True

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
        overflow = len(self._rows) - MAX_UNSAVED_METRICS
        if overflow <= 0:
            return
        dropped = self._rows[:overflow]
        del self._rows[:overflow]
        logger.error(
            "저장 못 한 1초 원자료가 너무 많아 오래된 것을 버린다 take=%s t_ms=%d~%d",
            self.take_id,
            dropped[0].t_ms,
            dropped[-1].t_ms,
        )
