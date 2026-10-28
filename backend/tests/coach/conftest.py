"""coach 테스트 공통: AI 예시 파일 읽기 · 시계."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pitch_coach_backend.coach.client import HttpCoachClient

EXAMPLES = Path(__file__).resolve().parents[3] / "ai/research/coach-agent/src/coach/examples"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# plan 은 AI 예시 파일이 없다. coach-agent.md 5-5 의 예시를 옮겼다
PLAN_RESPONSE: dict[str, Any] = {
    "coaching_plan": {
        "focus": [
            {"area": "GAZE", "slide_number": 3, "weight": 1.5, "why": "직전 Take 에서 대본 응시"}
        ],
        "relax": [{"area": "FILLER", "slide_number": 1, "why": "인사 장"}],
        "max_interventions": None,
    },
    "source_take_id": "take-122",
    "script_version_id": "sv-3",
    "fallback_reason": None,
    "dropped": [],
    "planner_hash": "0123456789ab",
    "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": "openai/gpt"},
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ai(handler, **kwargs: Any) -> HttpCoachClient:
    kwargs.setdefault("retry_delay_sec", 0)
    return HttpCoachClient("http://ai.test", transport=httpx.MockTransport(handler), **kwargs)


def error(status: int, code: str, message: str = "m") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


EVALUATE = {"take_id": "take-1", "t_ms": 92000}
FINALIZE = {"take_id": example("finalize/response.json")["take_result"]["take_id"], "t_ms": 92000}
PLAN = {"pitch_id": "p-1"}
