"""AI 를 부르는 백그라운드 호출의 자리. 프로세스 전체에서 같이 센다 (architecture.md 3절).

  - 코치 plan · finalize, Take 분석(채점 · 리뷰), 대본 파싱, 대본 등록(`/scripts/rubric`)이 합계
    `BACKGROUND_LIMIT` 자리를 나눠 쓴다. 긴 LLM 요청이 AI 스레드 풀을 채우면 1초마다 오는
    `/coach/evaluate` 가 300ms 를 넘긴다. evaluate 는 여기서 세지 않는다
  - 그 안에서 대본 등록은 `RUBRIC_LIMIT` 개까지다. 요청 하나가 장마다 LLM 을 동시에 12개 부른다
    (script-coverage-evaluation.md 8절)
  - 자리가 없으면 실패시키지 않고 기다린다. 부르는 쪽은 자리를 받은 뒤에 시도 시간 제한을 건다 —
    기다린 시간이 시간 초과로 이어지지 않는다. 대본 파싱만 만료(90초) 때문에 20초까지만 기다린다

자리를 쥔 채 자리를 또 잡지 않는다. 바깥에서 모든 자리를 쥐고 있으면 안쪽은 끝없이 기다린다.
BE 프로세스를 늘리면 프로세스마다 이만큼 잡으므로 합계가 넘지 않게 나눈다.
"""

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pitch_coach_backend.core.tasks import SemaphoreByLoop, loop_semaphore

BACKGROUND_LIMIT = 4

RUBRIC_LIMIT = 2

_background: SemaphoreByLoop = weakref.WeakKeyDictionary()
_rubric: SemaphoreByLoop = weakref.WeakKeyDictionary()
_standard: SemaphoreByLoop = weakref.WeakKeyDictionary()

STANDARD_LIMIT = 4


def background() -> asyncio.Semaphore:
    """백그라운드 AI 호출 하나의 자리."""
    return loop_semaphore(_background, BACKGROUND_LIMIT)


@asynccontextmanager
async def rubric() -> AsyncIterator[None]:
    """대본 등록 하나의 자리. 대본 등록 자리를 먼저 받고 합계 자리를 받는다 — 거꾸로 하면 합계
    자리를 쥔 채 기다려 다른 호출이 쓸 자리가 준다."""
    async with loop_semaphore(_rubric, RUBRIC_LIMIT), background():
        yield


def standard() -> asyncio.Semaphore:
    return loop_semaphore(_standard, STANDARD_LIMIT)
