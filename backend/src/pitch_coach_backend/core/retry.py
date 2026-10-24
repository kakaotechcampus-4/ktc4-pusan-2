"""DB 가 잠깐 안 될 때 같은 일을 몇 번 다시 하는 도우미 (요청 밖 백그라운드 작업용)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from pitch_coach_backend.core.database import is_transient_db_error

logger = logging.getLogger(__name__)


async def retry_transient[T](
    step: Callable[[], Awaitable[T]], delays: Sequence[float], *, what: str
) -> T:
    """`step` 을 DB 가 잠깐 안 될 때(`is_transient_db_error`)만 `delays` 간격으로 다시 한다. 다시
    해도 같은 실패이거나 다 써도 안 되면 그 예외를 올린다. `what` 은 로그에 남길 일 이름이다."""
    for delay in (*delays, None):
        try:
            return await step()
        except Exception as error:
            if delay is None or not is_transient_db_error(error):
                raise
            logger.warning("%s 실패. %.0f초 뒤 다시 한다", what, delay, exc_info=True)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
