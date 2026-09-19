"""final 전사 구간의 저장소. TakeStream 이 DB 를 직접 만지지 않게 하는 얇은 층이다.

TakeStream 은 WebSocket 요청보다 오래 사는 백그라운드 태스크라 `Depends(get_db)` 의
요청 스코프 세션을 쓸 수 없다 — 그 세션은 엔드포인트가 돌아오는 순간 닫힌다. 그래서
저장할 때마다 **짧은 세션을 새로 열고 커밋하고 닫는다.** 동기 SQLAlchemy 는 이벤트
루프를 막으므로 전부 threadpool 로 넘긴다 (인증 조회와 같은 이유).

저장 자체는 take 모듈의 service 가 한다 (realtime 은 entity 를 갖지 않는다). 테스트는
`dependencies.get_transcript_store` 를 가짜로 바꿔 끼운다.
"""

import uuid
from collections.abc import Callable
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


class DbTranscriptStore:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    async def cursor(self, take_id: uuid.UUID) -> tuple[int, int]:
        return await run_in_threadpool(self._cursor, take_id)

    async def append(self, take_id: uuid.UUID, segment: TranscriptSegmentCreateDTO) -> None:
        await run_in_threadpool(self._append, take_id, segment)

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
