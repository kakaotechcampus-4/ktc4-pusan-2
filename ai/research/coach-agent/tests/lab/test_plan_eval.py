"""코칭 계획 실험 도구 — 가짜 LLM 으로 기대 검사 · 반복 일관성 · 캐시 · 재생 비교가
맞게 도는지 본다.

실제 LLM 은 부르지 않는다 (tests/live/ 가 부른다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coach.config import load_config
from coach.planner import plan_message
from coach.schemas import PlanDraft
from coach_lab import llm as lab_llm
from coach_lab import plan_eval
from coach_lab.cache import SqlitePlanCache
from coach_lab.plan_eval import PLAN_SCENARIOS_DIR, evaluate_plans, plan_request
from coach_lab.simulator import Scenario, check_expect, run

PLAN_SCENARIOS = sorted(PLAN_SCENARIOS_DIR.glob("*.json"))

#: 시나리오마다 plan_expect 를 만족하는 초안 — 사람이 쓴 '정답 계획'
GOOD: dict[str, dict[str, Any]] = {
    "17_plan_numbers_slide": {
        "relax": [{"type": "GAZE", "slide_number": 3, "why": "수치를 정확히 읽어야 하는 장"}]
    },
    "18_plan_mission_focus": {
        "focus": [{"type": "TIME", "slide_number": 3, "weight": 1.5, "why": "이번 미션"}]
    },
    "19_plan_nothing_special": {},
    "20_plan_overcoached": {"max_interventions": 8},
    "21_plan_quoted_notice": {
        "relax": [{"type": "GAZE", "slide_number": 3, "why": "고지 원문을 그대로 읽어야 하는 장"}]
    },
}


class FixedLLM:
    """늘 같은 초안을 내는 LLM. 부른 횟수를 센다. 초안에 없는 필드는 빈 값으로 채운다."""

    def __init__(self, draft: dict[str, Any] | Exception) -> None:
        self.draft = draft
        self.calls = 0

    def invoke(self, messages: Any) -> PlanDraft:
        self.calls += 1
        if isinstance(self.draft, Exception):
            raise self.draft
        return PlanDraft.model_validate(
            {"focus": [], "relax": [], "max_interventions": None, **self.draft}
        )


def scenario(name: str) -> Scenario:
    return Scenario.load(PLAN_SCENARIOS_DIR / f"{name}.json")


def row_for(name: str, draft: dict[str, Any] | Exception, samples: int = 1) -> dict[str, Any]:
    report = evaluate_plans([scenario(name)], FixedLLM(draft), "m", None, samples)
    return report["scenarios"][0]


def test_plan_scenarios_are_all_known():
    assert [p.stem for p in PLAN_SCENARIOS] == sorted(GOOD)


@pytest.mark.parametrize("path", PLAN_SCENARIOS, ids=lambda p: p.stem)
def test_plan_scenarios_meet_expectations_without_a_plan(path: Path):
    """계획이 없을 때의 행동은 일반 시나리오처럼 expect 로 고정한다 — 계획이 바꾼 것만 보이게."""
    assert check_expect(run(Scenario.load(path))) == []


@pytest.mark.parametrize("name", sorted(GOOD))
def test_good_plans_pass(name: str):
    row = row_for(name, GOOD[name], samples=2)
    assert row["valid"] == [True, True]
    assert row["expect_failures"] == [[], []]
    assert row["consistent"] == [True, True]


def test_relaxed_slide_is_not_coached_in_the_replay():
    row = row_for("17_plan_numbers_slide", GOOD["17_plan_numbers_slide"])
    assert row["interventions_without_plan"].get("GAZE 3")
    assert "GAZE 3" not in row["interventions_with_plan"]
    assert row["plan_reasons_with_plan"]["PLAN_RELAXED"] > 0


@pytest.mark.parametrize(
    ("name", "draft", "want"),
    [
        ("17_plan_numbers_slide", {}, ["봐주기에 GAZE 3 가 없다", "계획으로 재생해도 GAZE 3"]),
        (
            "19_plan_nothing_special",
            {"relax": [{"type": "GAZE", "slide_number": 2, "why": "-"}]},
            ["근거 없는 봐주기"],
        ),
        ("20_plan_overcoached", {}, ["개입 상한이 없다"]),
    ],
    ids=["missed-relax", "unfounded-relax", "missed-budget"],
)
def test_plans_that_miss_the_expectation_are_reported(name, draft, want):
    failures = row_for(name, draft)["expect_failures"][0]
    assert len(failures) == len(want)
    for msg, prefix in zip(failures, want, strict=True):
        assert msg.startswith(prefix)


def test_unfounded_budget_is_dropped_and_counted():
    # 직전 Take 개입이 4번이었는데 2번으로 묶자는 초안 — 하한 5로 올리면 줄이는 게 없어 뺀다
    report = evaluate_plans(
        [scenario("18_plan_mission_focus")], FixedLLM({"max_interventions": 2}), "m", None, 1
    )
    row = report["scenarios"][0]
    assert row["plans"][0]["max_interventions"] is None
    assert "줄이지 못함" in row["dropped"][0][-1]
    assert report["summary"]["dropped_rate"] == 1.0


def test_plan_request_has_the_feature_doc_shape():
    req = plan_request(scenario("20_plan_overcoached"))
    assert req.pitch_id == "pitch-20_plan_overcoached"
    assert (req.source_take_id, req.script_version_id) == (None, None)
    assert len(req.previous_take_result["interventions"]) == 14
    assert set(req.previous_review) == {
        "overall_summary",
        "dimensions",
        "improvements",
        "mission_results",
    }
    mission = plan_request(scenario("18_plan_mission_focus"))
    assert [(r.area.value, r.slide_number) for r in mission.recurring_issues] == [("TIME", 3)]
    assert plan_request(scenario("19_plan_nothing_special")).previous_take_result is None


def test_scenario_config_reaches_the_planner():
    # 시나리오가 개입 상한 하한을 12 로 바꾸면 계획 검증도 그 값을 쓴다 (직전 Take 14번)
    sc = scenario("20_plan_overcoached").model_copy(
        update={"config": {"planner": {"min_interventions": 12}}}
    )
    report = evaluate_plans([sc], FixedLLM({"max_interventions": 8}), "m", None, 1)
    assert report["scenarios"][0]["plans"][0]["max_interventions"] == 12


def test_llm_failure_is_reported_not_raised():
    row = row_for("17_plan_numbers_slide", TimeoutError("slow"))
    assert row["valid"] == [False]
    assert row["fallback_reasons"] == ["LLM_TIMEOUT"]


def test_samples_are_cached_separately_and_reused(tmp_path: Path):
    cache = SqlitePlanCache(tmp_path / "cache.sqlite")
    sc = scenario("17_plan_numbers_slide")
    llm = FixedLLM(GOOD[sc.name])
    first = evaluate_plans([sc], llm, "m", cache, samples=2)
    assert (llm.calls, first["summary"]["llm_calls"]) == (2, 2)
    again = evaluate_plans([sc], llm, "m", cache, samples=3)
    assert (llm.calls, again["summary"]["llm_calls"]) == (3, 1)  # 늘린 반복만 새로 부른다
    assert again["summary"]["latency_ms_median"] is not None
    cache.close()


def test_scenario_conversion_is_pinned_in_the_llm_input():
    cfg = load_config().planner

    def take(name: str) -> dict[str, Any]:
        message = plan_message(plan_request(scenario(name)), cfg)
        return json.loads(message)["previous_take"]

    eighteen = take("18_plan_mission_focus")
    assert (eighteen["interventions"], eighteen["effective_rate"]) == (4, 0.5)
    assert eighteen["problem_seconds"] == {"TIME": 48.0} and eighteen["gave_up"] == []
    twenty = take("20_plan_overcoached")
    assert (twenty["interventions"], twenty["effective_rate"]) == (14, 0.14)
    assert len(twenty["gave_up"]) == 3
    assert twenty["problem_seconds"] == {"GAZE": 22.0, "SPEED": 18.0, "FILLER": 9.0}


def test_main_writes_the_new_result_shape(tmp_path: Path, monkeypatch):
    """main 을 LLM · 설정 · 캐시 대역으로 돌려 결과 파일 모양을 본다 (실제 LLM 은 부르지 않는다)."""

    class Settings:
        model = "fake-model"

    monkeypatch.setattr(lab_llm, "load_settings", lambda: Settings())
    monkeypatch.setattr(
        lab_llm, "plan_llm", lambda settings: FixedLLM(GOOD["17_plan_numbers_slide"])
    )
    out = tmp_path / "plan.json"
    scenario_path = str(PLAN_SCENARIOS_DIR / "17_plan_numbers_slide.json")
    argv = [scenario_path, "--samples", "1", "--no-cache", "--out", str(out)]
    assert plan_eval.main(argv) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "feature_version" in report and "policy_version" not in report
    plan = report["scenarios"][0]["plans"][0]
    assert set(plan) == {"focus", "relax", "max_interventions"}
    assert plan["relax"][0]["area"] == "GAZE" and "source" not in plan
    assert report["scenarios"][0]["valid"] == [True]
