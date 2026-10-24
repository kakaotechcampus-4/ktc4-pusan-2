"""final 전사 구간 · 1초 원자료 · 장 변경과 Take 종료의 저장소. TakeStream 이 DB 를 직접 만지지
않게 하는 얇은 층이다.

TakeStream 은 WebSocket 요청보다 오래 사는 백그라운드 태스크라 `Depends(get_db)` 의
요청 스코프 세션을 쓸 수 없다 — 그 세션은 엔드포인트가 돌아오는 순간 닫힌다. 그래서
저장할 때마다 **짧은 세션을 새로 열고 커밋하고 닫는다.** 동기 SQLAlchemy 는 이벤트
루프를 막으므로 전부 threadpool 로 넘긴다 (인증 조회와 같은 이유).

저장 자체는 take 모듈의 service 가 한다 (realtime 은 entity 를 갖지 않는다). 테스트는
`dependencies` 의 저장소를 가짜로 바꿔 끼운다.
"""

import uuid
from datetime import datetime
from typing import Literal, Protocol

from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionPerCall
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.take.dto import (
    MetricSecondCreateDTO,
    SlideEvent,
    TranscriptSegmentCreateDTO,
)


class TranscriptStore(Protocol):
    """TakeStream 이 의존하는 최소 인터페이스."""

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        """저장된 마지막 (seq, stt_session_no). 없으면 (0, 0)."""
        ...

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None: ...

    async def append_metrics(self, take_id: uuid.UUID, rows: list[MetricSecondCreateDTO]) -> None:
        """1초 원자료를 한 번에 저장한다. 이미 있는 1초는 건너뛴다."""
        ...

    async def append_slide_events(self, take_id: uuid.UUID, rows: list[SlideEvent]) -> None:
        """장 변경을 한 번에 저장한다. 같은 시각은 먼저 저장한 것을 둔다."""
        ...

    async def mark_ended(
        self,
        take_id: uuid.UUID,
        end_reason: str | None = None,
        utterance_ends: list[int] | None = None,
    ) -> bool:
        """Take 를 끝냈다 (상태 `ANALYZING` · 분석 단계 `FINALIZING`, 자동 종료면 이유도, Take 동안
        모은 문장 끝 신호도). 다시 붙으려는 연결을 막는 근거가 된다. 적었으면 True, Take 가 없거나
        이미 끝나 있었으면 False."""
        ...

    async def mark_abandoned(
        self,
        take_id: uuid.UUID,
        ended_at: datetime,
        end_reason: Literal["ABANDONED", "CLOSED"] = "ABANDONED",
        utterance_ends: list[int] | None = None,
    ) -> bool:
        """사용자가 떠난 Take 를 끝냈다 (상태 `FAILED`, 이유 `end_reason`, Take 동안 모은 문장 끝
        신호도). 적었으면 True."""
        ...

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        """`RUNNING` 인 Take 와 마지막으로 활동한 시각."""
        ...

    async def load_utterance_ends(self, take_id: uuid.UUID) -> list[int]:
        """Take 에 저장된 문장 끝 신호. 앱이 Take 도중에 다시 떴을 때 이어 받는다."""
        ...

    async def save_utterance_ends(self, take_id: uuid.UUID, utterance_ends: list[int]) -> None:
        """진행 중인 Take 의 문장 끝 신호를 적어 둔다 (앱을 내릴 때). 끝난 Take 는 그대로 둔다."""
        ...


class DbTranscriptStore(SessionPerCall):
    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        return await run_in_threadpool(self._with, take_service.transcript_cursor, take_id)

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await run_in_threadpool(
            self._with, take_service.append_transcript_segment, take_id, segment
        )

    async def append_metrics(self, take_id: uuid.UUID, rows: list[MetricSecondCreateDTO]) -> None:
        await run_in_threadpool(self._with, take_service.append_metric_seconds, take_id, rows)

    async def append_slide_events(self, take_id: uuid.UUID, rows: list[SlideEvent]) -> None:
        await run_in_threadpool(self._with, coach_service.append_slide_events, take_id, rows)

    async def mark_ended(
        self,
        take_id: uuid.UUID,
        end_reason: str | None = None,
        utterance_ends: list[int] | None = None,
    ) -> bool:
        return await run_in_threadpool(
            self._with, take_service.mark_ended, take_id, end_reason, utterance_ends
        )

    async def mark_abandoned(
        self,
        take_id: uuid.UUID,
        ended_at: datetime,
        end_reason: Literal["ABANDONED", "CLOSED"] = "ABANDONED",
        utterance_ends: list[int] | None = None,
    ) -> bool:
        return await run_in_threadpool(
            self._with, take_service.mark_abandoned, take_id, ended_at, end_reason, utterance_ends
        )

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        return await run_in_threadpool(self._with, take_service.running_takes)

    async def load_utterance_ends(self, take_id: uuid.UUID) -> list[int]:
        return await run_in_threadpool(self._with, take_service.stored_utterance_ends, take_id)

    async def save_utterance_ends(self, take_id: uuid.UUID, utterance_ends: list[int]) -> None:
        await run_in_threadpool(
            self._with, take_service.save_utterance_ends, take_id, utterance_ends
        )
