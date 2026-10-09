"""실제 LLM API 로 코칭 계획을 한 번 세운다 (과금, 1회 호출).

가짜 LLM 테스트(tests/unit/test_planner.py · tests/lab/test_plan_eval.py)는
계획 검증 · 대체 · 캐시를 본다.
이 테스트는 구조화 출력 LLM 을 만들고 부르는 경로가 실제로 동작하는지 확인한다.
기본 실행에서 빠진다 — `uv run python -m pytest -m live` 로 따로 돌린다.
"""

import pytest

from coach import plan_coaching
from coach_lab.llm import load_settings, plan_llm
from coach_lab.plan_eval import PLAN_SCENARIOS_DIR, plan_request
from coach_lab.simulator import Scenario

pytestmark = pytest.mark.live


def test_real_llm_returns_a_valid_plan():
    settings = load_settings()
    sc = Scenario.load(PLAN_SCENARIOS_DIR / "17_plan_numbers_slide.json")
    resp = plan_coaching(plan_request(sc), llm=plan_llm(settings), model=settings.model)
    assert resp.fallback_reason is None, "LLM 호출 또는 출력 형식이 실패했다"
    assert resp.plan.source == "LLM"
