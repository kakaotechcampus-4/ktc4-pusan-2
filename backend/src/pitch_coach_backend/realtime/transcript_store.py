"""final 전사 구간과 Take 종료의 저장소. TakeStream 이 DB 를 직접 만지지 않게 하는 얇은 층이다.

TakeStream 은 WebSocket 요청보다 오래 사는 백그라운드 태스크라 `Depends(get_db)` 의
요청 스코프 세션을 쓸 수 없다 — 그 세션은 엔드포인트가 돌아오는 순간 닫힌다. 그래서
저장할 때마다 **짧은 세션을 새로 열고 커밋하고 닫는다.** 동기 SQLAlchemy 는 이벤트
루프를 막으므로 전부 threadpool 로 넘긴다 (인증 조회와 같은 이유).

저장 자체는 take 모듈의 service 가 한다 (realtime 은 entity 를 갖지 않는다). 테스트는
`dependencies.get_transcript_store` 를 가짜로 바꿔 끼운다.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO


class TranscriptStore(Protocol):
    """TakeStream 이 의존하는 최소 인터페이스."""

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        """저장된 마지막 (seq, stt_session_no). 없으면 (0, 0)."""
        ...

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None: ...

    async def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
        """Take 를 끝냈다 (상태 `ANALYZING`, 자동 종료면 이유도). 다시 붙으려는 연결을 막는 근거가
        된다. 적었으면 True, Take 가 없거나 이미 끝나 있었으면 False."""
        ...

    async def mark_abandoned(self, take_id: uuid.UUID, ended_at: datetime) -> bool:
        """사용자가 떠난 Take 를 끝냈다 (상태 `FAILED`, 이유 `ABANDONED`). 적었으면 True."""
        ...

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        """`RUNNING` 인 Take 와 마지막으로 활동한 시각."""
        ...


class DbTranscriptStore:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        return await run_in_threadpool(self._cursor, take_id)

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await run_in_threadpool(self._append, take_id, segment)

    async def mark_ended(self, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
        return await run_in_threadpool(self._mark_ended, take_id, end_reason)

    async def mark_abandoned(self, take_id: uuid.UUID, ended_at: datetime) -> bool:
        return await run_in_threadpool(self._mark_abandoned, take_id, ended_at)

    async def running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        return await run_in_threadpool(self._running_takes)

    def _cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        db = self._session_factory()
        try:
            return take_service.transcript_cursor(db, take_id)
        finally:
            db.close()

    def _append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        db = self._session_factory()
        try:
            take_service.append_transcript_segment(db, take_id, segment)
        finally:
            db.close()

    def _mark_ended(self, take_id: uuid.UUID, end_reason: str | None) -> bool:
        db = self._session_factory()
        try:
            return take_service.mark_ended(db, take_id, end_reason)
        finally:
            db.close()

    def _mark_abandoned(self, take_id: uuid.UUID, ended_at: datetime) -> bool:
        db = self._session_factory()
        try:
            return take_service.mark_abandoned(db, take_id, ended_at)
        finally:
            db.close()

    def _running_takes(self) -> list[tuple[uuid.UUID, datetime]]:
        db = self._session_factory()
        try:
            return take_service.running_takes(db)
        finally:
            db.close()
