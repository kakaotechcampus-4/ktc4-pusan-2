"""동의 없이 FAILED 로 끝난 Take 의 1초 원자료를 7일 뒤 지우는 주기 작업.

`docs/ai/architecture.md` 1-1 '지우기 · 보관': 동의 없는 Take 의 1초 원자료는 Take 결과를 저장할 때
지운다. Take 결과를 만들지 못한 FAILED Take 는 나중에 replay 로 다시 만들 수 있게 끝난 시각부터 7일
두고, 그 뒤에 이 작업이 지운다. 지우기 직전에 아직 FAILED 인지 같은 문장에서 다시 본다.
"""

import asyncio
import logging
from datetime import UTC, datetime

from pitch_coach_backend.coach.store import CoachStore

logger = logging.getLogger(__name__)

# 지우는 간격. 기한(7일)에 비해 짧으면 된다
PURGE_INTERVAL_SEC = 60 * 60.0


async def purge_once(store: CoachStore) -> int:
    try:
        deleted = await store.purge_expired_raw(datetime.now(UTC))
    except Exception:
        logger.exception("기한이 지난 1초 원자료를 지우지 못했다. 다음 주기에 다시 한다")
        return 0
    if deleted:
        logger.info("기한이 지난 1초 원자료 %d행을 지웠다", deleted)
    return deleted


async def run_forever(store: CoachStore, interval_sec: float = PURGE_INTERVAL_SEC) -> None:
    while True:
        await purge_once(store)
        await asyncio.sleep(interval_sec)


def start(store: CoachStore) -> asyncio.Task[None]:
    return asyncio.create_task(run_forever(store), name="coach-raw-retention")
