"""AI 호출의 자리. 프로세스 전체에서 같이 센다 (architecture.md 3절).

  - 코치 plan · finalize, Take 분석(채점 · 리뷰), 대본 파싱, 대본 등록(`/scripts/rubric`)이 합계
    `BACKGROUND_LIMIT` 자리를 나눠 쓴다. 긴 LLM 요청이 AI 스레드 풀을 채우면 1초마다 오는
    `/coach/evaluate` 가 300ms 를 넘긴다. evaluate 는 여기서 세지 않는다
  - 그 안에서 대본 등록은 `RUBRIC_LIMIT` 개까지다. 요청 하나가 장마다 LLM 을 동시에 12개 부른다
    (script-coverage-evaluation.md 8절)
  - 자리가 없으면 실패시키지 않고 기다린다. 부르는 쪽은 자리를 받은 뒤에 시도 시간 제한을 건다 —
    기다린 시간이 시간 초과로 이어지지 않는다. 대본 파싱만 만료(90초) 때문에 20초까지만 기다린다
  - 대화 AI 요청(`/agent/turn` 한 번, 다시 평가의 리뷰 두 번)은 설정 `CHAT_AI_SLOTS_PER_PROCESS`
    자리를 따로 센다 (chat-agent.md 12-3 대화 자리). 자리를 기다린 시간도 실행 마감(120초)에
    들어간다. 프로세스 수 × 자리 수가 승인된 전체 상한을 넘거나 자리가 2보다 적으면 대화 기능을
    끈다 (`chat_slots_problem`, L2-13)

자리를 쥔 채 자리를 또 잡지 않는다. 바깥에서 모든 자리를 쥐고 있으면 안쪽은 끝없이 기다린다.
BE 프로세스를 늘리면 프로세스마다 이만큼 잡으므로 합계가 넘지 않게 나눈다.
"""

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.tasks import SemaphoreByLoop, loop_semaphore

BACKGROUND_LIMIT = 4

RUBRIC_LIMIT = 2

_background: SemaphoreByLoop = weakref.WeakKeyDictionary()
_rubric: SemaphoreByLoop = weakref.WeakKeyDictionary()
_standard: SemaphoreByLoop = weakref.WeakKeyDictionary()

STANDARD_LIMIT = 4

# 다시 평가는 대화 자리 두 개를 한 번에 잡는다(`chat_pair`). 이보다 적으면 끝없이 기다린다
CHAT_MIN_SLOTS = 2

_chat: SemaphoreByLoop = weakref.WeakKeyDictionary()
_chat_pairs: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


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


def chat() -> asyncio.Semaphore:
    """대화 AI 요청 하나의 자리. 백그라운드 자리와 따로 센다."""
    return loop_semaphore(_chat, settings.chat_ai_slots_per_process)


def chat_slots_problem() -> str | None:
    """대화 자리 설정이 어긋났으면 그 까닭, 맞으면 None. 어긋나면 대화 기능만 끈다."""
    per_process = settings.chat_ai_slots_per_process
    # uvicorn 은 워커 수가 1 이하면 프로세스 하나로 뜬다. 0 을 그대로 곱하면 검사가 빠진다
    processes = max(settings.web_concurrency, 1)
    total = settings.chat_ai_slots_total
    if per_process < CHAT_MIN_SLOTS:
        return f"CHAT_AI_SLOTS_PER_PROCESS={per_process} 는 {CHAT_MIN_SLOTS} 이상이어야 합니다"
    if processes * per_process > total:
        return (
            f"프로세스 {processes}개(WEB_CONCURRENCY) × CHAT_AI_SLOTS_PER_PROCESS={per_process} 가 "
            f"CHAT_AI_SLOTS_TOTAL={total} 를 넘습니다"
        )
    return None


@asynccontextmanager
async def chat_pair() -> AsyncIterator[None]:
    """다시 평가(리뷰 두 번을 함께 보냄)의 대화 자리 두 개를 한 번에 잡는다.

    하나씩 잡으면 두 다시 평가가 한 자리씩 쥔 채 서로 기다릴 수 있다. 두 자리를 잡는 동안은 다른
    다시 평가가 끼어들지 못하게 줄을 세운다.
    """
    loop = asyncio.get_running_loop()
    slots = chat()
    if (lock := _chat_pairs.get(loop)) is None:
        lock = _chat_pairs[loop] = asyncio.Lock()
    async with lock:
        await slots.acquire()
        try:
            await slots.acquire()
        except BaseException:
            slots.release()
            raise
    try:
        yield
    finally:
        slots.release()
        slots.release()
