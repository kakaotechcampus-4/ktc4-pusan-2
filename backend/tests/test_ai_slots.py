"""백그라운드 AI 호출 자리 (`core/ai_slots.py`, architecture.md 3절).

코치 plan · finalize, Take 분석, 대본 등록이 합계 4 자리를 나눠 쓰고, 그 안에서 대본 등록은
2개까지다. 넘는 요청은 실패하지 않고 기다린다.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.module.pitch.adapter.script_rubric import RUBRIC_PATH, HttpRubricClient
from pitch_coach_backend.module.pitch.dto import RubricTicket
from pitch_coach_backend.module.pitch.service.script_rubric_runner import ScriptRubricRunner
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.coach.conftest import PLAN, PLAN_RESPONSE, ai

pytestmark = pytest.mark.anyio

BASE_URL = "http://ai.test"
META = {"schema_version": "1.0", "feature_version": "script_coverage-1.0", "model": "test-model"}
# 측정에서 시간 초과가 몰린 30장 대본
SLIDES = tuple((number, f"{number}번 장 대본") for number in range(1, 31))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _ticket() -> RubricTicket:
    return RubricTicket(
        script_version_id=uuid.uuid4(),
        pitch_id=uuid.uuid4(),
        requested_at=datetime.now(UTC),
        slides=SLIDES,
        missing=tuple(number for number, _ in SLIDES),
        retry_used=False,
    )


def _rubric_body() -> dict[str, Any]:
    return {
        "rubrics": [
            {"meta": {"slide_number": number}, "core_claim": "주장"} for number, _ in SLIDES
        ],
        "failed": [],
        "meta": META,
    }


class Peaks:
    """AI 역할. 경로별 · 전체 동시 요청 수의 최댓값을 센다."""

    def __init__(self, delay_sec: float) -> None:
        self.delay_sec = delay_sec
        self.running: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.total = 0
        self.peak_total = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.running[path] = self.running.get(path, 0) + 1
        self.total += 1
        self.peak[path] = max(self.peak.get(path, 0), self.running[path])
        self.peak_total = max(self.peak_total, self.total)
        try:
            await asyncio.sleep(self.delay_sec)
        finally:
            self.running[path] -= 1
            self.total -= 1
        if path == RUBRIC_PATH:
            return httpx.Response(200, json=_rubric_body())
        return httpx.Response(200, json=PLAN_RESPONSE)


def _rubric_runner(handler: Peaks, attempt_timeout_sec: float = 130.0) -> ScriptRubricRunner:
    client = HttpRubricClient(
        BASE_URL,
        attempt_timeout_sec=attempt_timeout_sec,
        transport=httpx.MockTransport(handler),
    )

    def no_db() -> Any:
        raise AssertionError("AI 호출만 본다")

    return ScriptRubricRunner(client, session_factory=no_db)


async def test_four_scripts_at_once_send_two_rubrics_within_the_total() -> None:
    handler = Peaks(delay_sec=0.05)
    runner = _rubric_runner(handler)
    coach = ai(handler)

    replies = await asyncio.gather(
        *(runner._call(_ticket(), None, uuid.uuid4().hex) for _ in range(4)),
        *(coach.plan(PLAN) for _ in range(4)),
    )

    # 넘는 요청은 실패하지 않고 기다렸다 보낸다
    assert all(reply is not None for reply in replies)
    assert all(len(reply.rubrics) == 30 for reply in replies[:4])
    assert handler.peak[RUBRIC_PATH] == ai_slots.RUBRIC_LIMIT
    assert handler.peak_total == ai_slots.BACKGROUND_LIMIT


async def test_rubric_waiting_for_its_slot_leaves_the_total_to_others() -> None:
    hold = asyncio.Event()
    held = 0

    async def rubric_job() -> None:
        nonlocal held
        async with ai_slots.rubric():
            held += 1
            await hold.wait()

    rubrics = [asyncio.create_task(rubric_job()) for _ in range(3)]
    await asyncio.sleep(0.01)
    assert held == ai_slots.RUBRIC_LIMIT

    # 세 번째 대본 등록은 대본 등록 자리를 기다린다. 합계 4 중 남은 2 자리는 다른 호출이 쓴다
    async with asyncio.timeout(1), ai_slots.background(), ai_slots.background():
        pass

    hold.set()
    await asyncio.gather(*rubrics)
    assert held == 3


async def test_time_waiting_for_a_slot_is_not_an_attempt_timeout() -> None:
    # 시도 하나는 0.2초라 마감(0.3초) 안이다. 뒤의 둘은 앞 둘이 끝나기를 0.2초 기다렸다 보낸다 —
    # 기다린 시간까지 세면 0.4초라 마감을 넘는다
    handler = Peaks(delay_sec=0.2)
    runner = _rubric_runner(handler, attempt_timeout_sec=0.3)

    replies = await asyncio.gather(
        *(runner._call(_ticket(), None, uuid.uuid4().hex) for _ in range(4))
    )

    assert all(len(reply.rubrics) == 30 for reply in replies)


async def test_take_analysis_waits_for_a_total_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_slots, "BACKGROUND_LIMIT", 1)
    scored: list[uuid.UUID] = []

    async def analyze(take_id: uuid.UUID, session_factory: Any) -> str:
        scored.append(take_id)
        return "SKIPPED"

    monkeypatch.setattr(coverage_service, "analyze_script_coverage", analyze)
    take_id = uuid.uuid4()

    async with ai_slots.background():
        # 코치 호출 같은 다른 백그라운드 호출이 자리를 다 쓰고 있다
        scoring = asyncio.create_task(analysis_runner._score(take_id, None))
        await asyncio.sleep(0.01)
        assert scored == []

    assert await scoring == "SKIPPED"
    assert scored == [take_id]
