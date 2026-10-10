"""코칭 계획 — LLM 초안을 검증해 계획으로 만들고, 실패하면 기본 계획으로 돌아간다 (가짜 LLM)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from coach import decide, plan_coaching
from coach.config import load_config
from coach.planner import plan_message, planner_hash
from coach.schemas import PlanDraft, PlanRequest

from .conftest import PLAN, gaze_script, make_request

SCRIPTS = [
    {"slide_number": 1, "script": "안녕하세요. 오늘은 로컬 처리 기반 코칭을 소개합니다."},
    {"slide_number": 3, "script": "매출은 2024년 3분기 12억 4천만 원, 전년 대비 18.2% 늘었습니다."},
]


class FakeLLM:
    """출력 스키마가 고정된 LLM 흉내. 부른 횟수와 받은 메시지를 남긴다."""

    def __init__(self, output: dict[str, Any] | Exception) -> None:
        self.output = output
        self.calls: list[Any] = []

    def invoke(self, messages: list[tuple[str, str]]) -> PlanDraft:
        self.calls.append(messages)
        if isinstance(self.output, Exception):
            raise self.output
        return PlanDraft.model_validate(self.output)


class DictCache:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get(self, key: str, planner_hash: str) -> str | None:
        return self.store.get((key, planner_hash))

    def put(self, key: str, planner_hash: str, output_json: str) -> None:
        self.store[(key, planner_hash)] = output_json


def request(**kw: Any) -> dict[str, Any]:
    return {"take_id": "t1", "plan": PLAN, "scripts": SCRIPTS, **kw}


def draft(focus=(), relax=(), max_interventions=None) -> dict[str, Any]:
    return {"focus": list(focus), "relax": list(relax), "max_interventions": max_interventions}


GAZE_3 = {"type": "GAZE", "slide_number": 3, "why": "수치를 정확히 읽어야 한다"}
OVERCOACHED = {"summary": {"interventions": 9, "effective_rate": 0.1}}


def test_llm_plan_is_validated_and_goes_into_the_first_coach_state():
    llm = FakeLLM(
        draft(
            focus=[{"type": "TIME", "slide_number": 3, "weight": 1.5, "why": "미션"}],
            relax=[GAZE_3],
        )
    )
    resp = plan_coaching(request(), llm=llm, model="m")
    assert resp.fallback_reason is None and resp.dropped == []
    assert resp.plan.source == "LLM"
    assert [(f.area.value, f.slide_number, f.weight) for f in resp.plan.focus] == [("TIME", 3, 1.5)]
    assert resp.coach_state["plan"]["relax"][0]["slide_number"] == 3
    assert len(llm.calls) == 1


def test_relaxed_gaze_is_not_coached_on_that_slide_only():
    resp = plan_coaching(request(), llm=FakeLLM(draft(relax=[GAZE_3])), model="m")
    state = resp.coach_state
    on_3 = decide(make_request(20_000, slide=3, gaze=gaze_script(0.9), state=state))
    c = {c.issue_type.value: c for c in on_3.candidates}["GAZE_ON_SCRIPT"]
    assert c.status.value == "IGNORED" and "PLAN_RELAXED" in c.reasons
    on_2 = decide(make_request(20_000, slide=2, gaze=gaze_script(0.9), state=state))
    c = {c.issue_type.value: c for c in on_2.candidates}["GAZE_ON_SCRIPT"]
    assert "PLAN_RELAXED" not in c.reasons


@pytest.mark.parametrize(
    ("relax", "why"),
    [
        ({"type": "TIME", "slide_number": 3, "why": "-"}, "봐줄 수 없는 영역"),
        ({"type": "GAZE", "slide_number": 9, "why": "-"}, "계획에 없는 장"),
        ({"type": "FILLER", "slide_number": 2, "why": "-"}, "이번 미션 영역"),
    ],
)
def test_unsafe_relax_is_dropped_with_a_reason(relax, why):
    missions = [{"mission_id": "m1", "area": "FILLER", "slide_number": None}]
    resp = plan_coaching(request(missions=missions), llm=FakeLLM(draft(relax=[relax])), model="m")
    assert resp.plan.relax == []
    assert len(resp.dropped) == 1 and why in resp.dropped[0]


def test_weights_counts_and_budget_are_clamped():
    focus = [
        {"type": "GAZE", "slide_number": None, "weight": 9.0, "why": "-"},
        {"type": "GAZE", "slide_number": None, "weight": 1.5, "why": "-"},  # 중복
        {"type": "SPEED", "slide_number": None, "weight": 0.1, "why": "-"},
        {"type": "VOLUME", "slide_number": None, "weight": 1.3, "why": "-"},
        {"type": "FILLER", "slide_number": None, "weight": 1.3, "why": "-"},  # 4개째
    ]
    relax = [{"type": "GAZE", "slide_number": 3, "why": "-"}]  # 전체 GAZE 집중과 겹침
    resp = plan_coaching(
        request(previous_review=OVERCOACHED),
        llm=FakeLLM(draft(focus, relax, max_interventions=1)),
        model="m",
    )
    assert [(f.area.value, f.weight) for f in resp.plan.focus] == [
        ("GAZE", 2.0),
        ("SPEED", 0.5),
        ("VOLUME", 1.3),
    ]
    assert resp.plan.relax == []
    assert resp.plan.max_interventions == 5
    assert len(resp.dropped) == 4


@pytest.mark.parametrize(
    ("review", "budget", "want", "why"),
    [
        (OVERCOACHED, 6, 6, None),
        (None, 6, None, "직전 리뷰의 개입 수가 없어"),
        ({"summary": {"interventions": 4, "effective_rate": 0.5}}, 2, None, "줄이지 못함"),
    ],
    ids=["fewer-than-before", "no-previous-review", "not-fewer-after-raising"],
)
def test_budget_only_when_it_says_less_than_last_take(review, budget, want, why):
    llm = FakeLLM(draft(max_interventions=budget))
    resp = plan_coaching(request(previous_review=review), llm=llm, model="m")
    assert resp.plan.max_interventions == want
    if why is not None:
        assert why in resp.dropped[-1]


def test_message_counts_numbers_per_slide():
    message = plan_message(PlanRequest.model_validate(request()), load_config().planner)
    counts = {s["slide_number"]: s["number_count"] for s in json.loads(message)["slides"]}
    assert counts[1] == 0 and counts[3] == 5  # 2024 · 3 · 12 · 4 · 18.2


@pytest.mark.parametrize(
    ("kw", "llm", "reason"),
    [
        ({"mode": "EXAM"}, FakeLLM(draft()), "EXAM_MODE"),
        ({}, None, "NO_LLM"),
        ({}, FakeLLM(TimeoutError("slow")), "LLM_ERROR"),
        ({}, FakeLLM({"focus": "not a list"}), "LLM_ERROR"),
    ],
    ids=["exam", "no-llm", "timeout", "invalid-output"],
)
def test_fallback_is_the_default_plan(kw, llm, reason):
    resp = plan_coaching(request(**kw), llm=llm, model="m")
    assert resp.fallback_reason == reason
    assert resp.plan.source == "DEFAULT" and resp.plan.focus == [] and resp.plan.relax == []
    # 기본 계획의 coach_state 로 하는 판단은 계획 없이 시작한 것과 같다
    with_plan = decide(make_request(20_000, gaze=gaze_script(0.9), state=resp.coach_state))
    without = decide(make_request(20_000, gaze=gaze_script(0.9)))
    assert with_plan.candidates == without.candidates


def test_cache_answers_the_same_question_without_calling_the_llm():
    cache = DictCache()
    llm = FakeLLM(draft(relax=[GAZE_3]))
    first = plan_coaching(request(), llm=llm, model="m", cache=cache)
    second = plan_coaching(request(), llm=llm, model="m", cache=cache)
    assert len(llm.calls) == 1
    assert first.plan == second.plan
    # 모델이 바뀌면 다른 질문이다
    plan_coaching(request(), llm=llm, model="other", cache=cache)
    assert len(llm.calls) == 2


class BrokenCache:
    """읽으면 깨진 값을 주고, 쓰면 예외를 내는 캐시."""

    def get(self, key: str, planner_hash: str) -> str | None:
        return "{not json"

    def put(self, key: str, planner_hash: str, output_json: str) -> None:
        raise OSError("disk full")


def test_broken_cache_is_a_miss_not_a_failure():
    llm = FakeLLM(draft(relax=[GAZE_3]))
    resp = plan_coaching(request(), llm=llm, model="m", cache=BrokenCache())
    assert resp.fallback_reason is None and resp.plan.source == "LLM"
    assert len(llm.calls) == 1


def test_message_carries_scripts_missions_and_previous_issues():
    review = {
        "issues": [{"rank": 1, "area": "GAZE", "slide_number": 3, "burden_s": 40.0}],
        "type_status": [{"area": "GAZE", "status": "PRIORITY"}],
        "summary": {"interventions": 9, "effective_rate": 0.2},
    }
    req = request(
        missions=[{"mission_id": "m1", "area": "TIME", "slide_number": 3}],
        previous_review=review,
    )
    llm = FakeLLM(draft())
    plan_coaching(req, llm=llm, model="m")
    message = llm.calls[0][1][1]
    assert "18.2%" in message and '"type": "TIME"' in message and '"PRIORITY"' in message
    assert message == plan_message(PlanRequest.model_validate(req), load_config().planner)
    assert planner_hash("m") != planner_hash("other")
