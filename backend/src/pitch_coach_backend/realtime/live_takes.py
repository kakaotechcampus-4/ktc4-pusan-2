"""HTTP 도메인 요청을 실시간 Take 처리에 연결한다."""
import uuid
from collections.abc import Iterable

import anyio

from pitch_coach_backend.module.hooks import MarkEnded
from pitch_coach_backend.realtime import take_stream


class RealtimeLiveTakes:
    def discard(self, take_ids: Iterable[uuid.UUID]) -> None:
        anyio.from_thread.run(take_stream.discard, list(take_ids))

    def is_live(self, take_id: uuid.UUID) -> bool:
        return anyio.from_thread.run_sync(take_stream.is_live, take_id)

    def end_closed_tab(self, take_id: uuid.UUID) -> None:
        anyio.from_thread.run(take_stream.end_closed_tab, take_id)

    def end(self, take_id: uuid.UUID, mark: MarkEnded) -> None:
        if anyio.from_thread.run(take_stream.end_without_stop, take_id):
            mark(None, None)
