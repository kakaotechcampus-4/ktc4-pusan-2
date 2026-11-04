"""발표 중 원자료(WS `metrics` 의 1초 원자료, WS `slide` 의 장 변경)를 모았다가 한 번에 저장한다.

1초마다 DB 를 왕복하면 Take 하나가 한 시간에 3,600번 커넥션을 빌린다. 그래서 1초 원자료는
`METRICS_BATCH` 개가 모이면 백그라운드로 한 번에 넣는다 (`docs/ai/architecture.md` 1-3 "몇 초씩
모아 저장한다"). 장 변경은 드물어 받는 대로 넣는다. 저장 · 다시 넣기 규칙은 둘이 같다
(`core.row_buffer.RowBuffer`).

버퍼는 스트림이 아니라 Take 에 붙어 있다 (`stream/record.TakeRecord`). grace 가 지나 스트림이 새로
열려도 앞 스트림이 못 넣은 행이 이어진다.

Take 를 끝낼 때(`ANALYZING`)는 그 전에 `seal` 하고 `flush` 를 기다린다. Take 뒤 분석이 원자료를
읽으므로 그때 다 있어야 하고, 그 뒤에 온 행은 받지 않는다. 앱 종료 때 남은 행은
`stream/registry.stop_all` 이 넣는다.

Take 하나에서 받는 1초 원자료는 `MAX_METRICS_PER_TAKE` 개까지다 (`MetricBuffer.admit`). 메시지
하나의 크기와 `t_ms` 범위만 보면 `t_ms` 를 1ms 씩 바꿔 보내는 것만으로 행이 수백만 개 쌓인다.
"""

import logging
import uuid

from pitch_coach_backend.core.row_buffer import RowBuffer
from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO, SlideEvent
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.realtime.storage.store import TranscriptStore

# 이만큼 모이면 저장한다. FE 는 1초에 하나를 보내므로 5초마다다
METRICS_BATCH = 5
# 저장하지 못해 들고 있을 행 수. 넘치면 오래된 것부터 버린다. 1초에 하나라 10분이다
MAX_UNSAVED_METRICS = 600
# Take 하나에서 받을 1초 원자료 수. Take 시각 상한(`dto.MAX_METRICS_T_MS`, 1시간 + 60초)의 1초
# 3,660개에 새로고침 · 끝에서 생기는 1초보다 짧은 조각의 여유를 더했다
MAX_METRICS_PER_TAKE = 4_000

logger = logging.getLogger(__name__)


class MetricBuffer(RowBuffer[MetricSecondCreateDTO]):
    """1초 원자료 (take_metric_seconds). `METRICS_BATCH` 개씩 넣는다."""

    def __init__(self, take_id: uuid.UUID, store: TranscriptStore) -> None:
        super().__init__(
            take_id,
            store.append_metrics,
            what="1초 원자료",
            batch=METRICS_BATCH,
            key=lambda row: row.t_ms,
            max_unsaved=MAX_UNSAVED_METRICS,
            gone_errors=(NonExistentTake,),
        )
        # 받은 1초의 `t_ms`. `MAX_METRICS_PER_TAKE` 개를 넘지 않는다. Take 를 끝내면 비운다 — 끝난
        # Take 의 기록도 프로세스에 한동안 남는다 (`state.takes`)
        self._admitted: set[int] = set()
        self._capped = False

    def admit(self, t_ms: int) -> bool:
        """이 1초를 받는다. 이미 받은 1초거나(먼저 받은 것을 둔다) Take 의 상한을 넘었으면 False.

        저장 · 코치에 넘기기 전에 부른다 (`TakeStream.push_metrics`). 코치의 창 · 버퍼
        (`CoachSession`)도 여기를 지난 것만 받아서 같이 묶인다.

        간격 · 순서는 보지 않는다. 끊긴 동안 쌓아 둔 1초는 다시 붙은 뒤 몰아서 오고, 새로고침해
        이어받은 시각이 추정값이면 앞서 받은 1초와 겹치거나 촘촘하게 온다 — 둘 다 정상 기록이다.
        벽시계 간격으로 거르면 이것들을 잃는다.
        """
        if self._sealed or t_ms in self._admitted:
            return False
        if len(self._admitted) >= MAX_METRICS_PER_TAKE:
            if not self._capped:
                self._capped = True
                logger.warning(
                    "1초 원자료가 상한을 넘어 버린다 take=%s limit=%d",
                    self.owner_id,
                    MAX_METRICS_PER_TAKE,
                )
            return False
        self._admitted.add(t_ms)
        return True

    def seal(self) -> None:
        super().seal()
        self._admitted.clear()

    def close(self) -> int:
        self._admitted.clear()
        return super().close()


class SlideBuffer(RowBuffer[SlideEvent]):
    """장 변경 (take_slide_events). 받는 대로 넣는다."""

    def __init__(self, take_id: uuid.UUID, store: TranscriptStore) -> None:
        super().__init__(
            take_id,
            store.append_slide_events,
            what="장 변경",
            batch=1,
            key=lambda event: event.started_ms,
            max_unsaved=MAX_UNSAVED_METRICS,
            gone_errors=(NonExistentTake,),
        )
