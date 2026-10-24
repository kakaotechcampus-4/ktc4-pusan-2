"""발표 중 원자료(WS `metrics` 의 1초 원자료, WS `slide` 의 장 변경)를 모았다가 한 번에 저장한다.

1초마다 DB 를 왕복하면 Take 하나가 한 시간에 3,600번 커넥션을 빌린다. 그래서 1초 원자료는
`METRICS_BATCH` 개가 모이면 백그라운드로 한 번에 넣는다 (`docs/ai/architecture.md` 1-3 "몇 초씩
모아 저장한다"). 장 변경은 드물어 받는 대로 넣는다. 저장 · 다시 넣기 규칙은 둘이 같다
(`core.row_buffer.RowBuffer`).

버퍼는 스트림이 아니라 Take 에 붙어 있다 (`take_stream._TakeRecord`). grace 가 지나 스트림이 새로
열려도 앞 스트림이 못 넣은 행이 이어진다.

Take 를 끝낼 때(`ANALYZING`)는 그 전에 `seal` 하고 `flush` 를 기다린다. Take 뒤 분석이 원자료를
읽으므로 그때 다 있어야 하고, 그 뒤에 온 행은 받지 않는다. 앱 종료 때 남은 행은
`take_stream.stop_all` 이 넣는다.
"""

import uuid

from pitch_coach_backend.core.row_buffer import RowBuffer
from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.realtime.transcript_store import TranscriptStore

# 이만큼 모이면 저장한다. FE 는 1초에 하나를 보내므로 5초마다다
METRICS_BATCH = 5
# 저장하지 못해 들고 있을 행 수. 넘치면 오래된 것부터 버린다. 1초에 하나라 10분이다
MAX_UNSAVED_METRICS = 600


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
