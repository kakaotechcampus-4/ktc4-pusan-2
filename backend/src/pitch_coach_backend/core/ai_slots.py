"""AI 를 부르는 백그라운드 호출의 자리. 프로세스 전체에서 같이 센다 (architecture.md 3절).

  - 코치 plan · finalize, Take 분석(채점 · 리뷰), 대본 등록(`/scripts/rubric`)이 합계
    `BACKGROUND_LIMIT` 자리를 나눠 쓴다. 긴 LLM 요청이 AI 스레드 풀을 채우면 1초마다 오는
    `/coach/evaluate` 가 300ms 를 넘긴다. evaluate 는 여기서 세지 않는다
  - 그 안에서 대본 등록은 `RUBRIC_LIMIT` 개까지다. 요청 하나가 장마다 LLM 을 동시에 12개 부른다
    (script-coverage-evaluation.md 8절)
  - 자리가 없으면 실패시키지 않고 기다린다. 부르는 쪽은 자리를 받은 뒤에 시도 시간 제한을 건다 —
    기다린 시간이 시간 초과로 이어지지 않는다

자리를 쥔 채 자리를 또 잡지 않는다. 바깥에서 모든 자리를 쥐고 있으면 안쪽은 끝없이 기다린다.
BE 프로세스를 늘리면 프로세스마다 이만큼 잡으므로 합계가 넘지 않게 나눈다.
"""

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

BACKGROUND_LIMIT = 4

RUBRIC_LIMIT = 2

type _ByLoop = weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]

# 이벤트 루프마다 하나. asyncio.Semaphore 는 처음 기다린 루프에 묶여서, 하나를 여러 루프가
# 쓰면(테스트의 TestClient 마다 루프가 다르다) 다른 루프에서 기다리는 순간 RuntimeError 다.
# 운영은 uvicorn 루프 하나라 프로세스 전체에 하나다
_background: _ByLoop = weakref.WeakKeyDictionary()
_rubric: _ByLoop = weakref.WeakKeyDictionary()
_standard: _ByLoop = weakref.WeakKeyDictionary()

STANDARD_LIMIT = 4


def background() -> asyncio.Semaphore:
    """백그라운드 AI 호출 하나의 자리."""
    return _slots(_background, BACKGROUND_LIMIT)


@asynccontextmanager
async def rubric() -> AsyncIterator[None]:
    """대본 등록 하나의 자리. 대본 등록 자리를 먼저 받고 합계 자리를 받는다 — 거꾸로 하면 합계
    자리를 쥔 채 기다려 다른 호출이 쓸 자리가 준다."""
    async with _slots(_rubric, RUBRIC_LIMIT), background():
        yield


def standard() -> asyncio.Semaphore:
    return _slots(_standard, STANDARD_LIMIT)


def _slots(by_loop: _ByLoop, limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (slots := by_loop.get(loop)) is None:
        slots = asyncio.Semaphore(limit)
        by_loop[loop] = slots
    return slots
