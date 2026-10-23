"""발표 중 1초 원자료(WS `metrics`)를 모았다가 몇 초씩 한 번에 저장한다.

1초마다 DB 를 왕복하면 Take 하나가 한 시간에 3,600번 커넥션을 빌린다. 그래서 `METRICS_BATCH`
개가 모이면 백그라운드로 한 번에 넣는다 (`docs/ai/architecture.md` 1-3 "몇 초씩 모아 저장한다").

버퍼는 스트림이 아니라 Take 에 붙어 있다 (`take_stream._TakeRecord`). grace 가 지나 스트림이 새로
열려도 앞 스트림이 못 넣은 행이 이어진다.

Take 를 끝낼 때(`ANALYZING`)는 그 전에 `flush` 를 기다린다. Take 뒤 분석이 원자료를 읽으므로
그때 다 있어야 한다. 저장에 실패한 행은 들고 있다가 다음 저장 때 다시 넣는다.
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

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, row: MetricSecondCreateDTO) -> None:
        if self._gone:
            return
        self._rows.append(row)
        self._trim()
        if len(self._rows) >= METRICS_BATCH:
            self.flush_soon()

    def flush_soon(self) -> None:
        """백그라운드로 저장한다. 이미 저장 중이면 그 저장이 새로 온 행까지 넣는다."""
        if not self._rows or (self._task is not None and not self._task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 밖이다 (앱이 내려간 뒤의 정리). 앱 종료는 `take_stream.stop_all` 이 넣는다
            return
        self._task = loop.create_task(self.flush(), name=f"take-metrics:{self.take_id}")

    async def flush(self) -> bool:
        """들고 있는 행을 모두 저장한다. 다 넣었으면 True.

        실패하면 그 행을 되돌려 두고 멈춘다 — 다음 `add` 나 Take 를 끝낼 때 다시 넣는다.
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
                    return False
                except BaseException as e:
                    self._rows = rows + self._rows
                    self._trim()
                    if not isinstance(e, Exception):
                        raise
                    logger.exception(
                        "1초 원자료 저장 실패 take=%s 남은 개수=%d", self.take_id, len(self._rows)
                    )
                    return False
            return True

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
