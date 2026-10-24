"""코칭 계획 — LLM 초안을 검증해 계획으로 만들고, 실패하면 기본 계획으로 돌아간다 (가짜 LLM)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from coach import plan_coaching
from coach.config import load_config
from coach.planner import plan_message, planner_hash
from coach.schemas import CoachingPlan, PlanDraft, PlanRequest

from .conftest import PLAN, decide_seen, make_request
from .fakes import ScriptedJudge, fake_issue, fake_judges

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
    return {"pitch_id": "p1", "plan": PLAN, "scripts": SCRIPTS, **kw}


def draft(focus=(), relax=(), max_interventions=None) -> dict[str, Any]:
    return {"focus": list(focus), "relax": list(relax), "max_interventions": max_interventions}


GAZE_3 = {"type": "GAZE", "slide_number": 3, "why": "수치를 정확히 읽어야 한다"}


def take_result(n: int, effective: int = 0, ineffective: int | None = None, **kw: Any) -> dict:
    """개입 n개(효과 있음 effective개, 효과 없음 ineffective개, 나머지는 못 잼)인 Take 결과."""
    ineffective = n - effective if ineffective is None else ineffective
    outcomes = ["EFFECTIVE"] * effective + ["INEFFECTIVE"] * ineffective
    outcomes += ["NOT_MEASURED"] * (n - len(outcomes))
    ivs = [{"intervention_id": f"iv-{i}", "outcome": o} for i, o in enumerate(outcomes)]
    return {"interventions": ivs, **kw}


OVERCOACHED = take_result(9, effective=1)


def test_llm_plan_is_validated_and_returned_as_plan():
    llm = FakeLLM(
        draft(
            focus=[{"type": "TIME", "slide_number": 3, "weight": 1.5, "why": "미션"}],
            relax=[GAZE_3],
        )
    )
    resp = plan_coaching(request(), llm=llm, model="m")
    assert resp.fallback_reason is None and resp.dropped == []
    assert resp.meta.model == "m"
    assert [(f.area.value, f.slide_number, f.weight) for f in resp.coaching_plan.focus] == [
        ("TIME", 3, 1.5)
    ]
    assert resp.coaching_plan.relax[0].slide_number == 3
    assert not hasattr(resp, "coach_state")
    assert len(llm.calls) == 1


def _gaze_judges():
    issue = fake_issue("GAZE", "GAZE_ON_SCRIPT", 0.9)
    gaze = ScriptedJudge("gaze", lambda t: {"issues": [issue], "metrics": {"script_ratio": 0.9}})
    return fake_judges(gaze=gaze)


def test_relaxed_gaze_is_not_coached_on_that_slide_only():
    resp = plan_coaching(request(), llm=FakeLLM(draft(relax=[GAZE_3])), model="m")
    plan = resp.coaching_plan.model_dump(mode="json")
    _, on_3 = decide_seen(make_request(20_000, slide=3, coaching_plan=plan), _gaze_judges())
    c = {c.issue_type.value: c for c in on_3}["GAZE_ON_SCRIPT"]
    assert c.status.value == "IGNORED" and "PLAN_RELAXED" in c.reasons
    _, on_2 = decide_seen(make_request(20_000, slide=2, coaching_plan=plan), _gaze_judges())
    c = {c.issue_type.value: c for c in on_2}["GAZE_ON_SCRIPT"]
    assert "PLAN_RELAXED" not in c.reasons


@pytest.mark.parametrize(
    ("relax", "why"),
    [
        ({"type": "TIME", "slide_number": 3, "why": "-"}, "봐줄 수 없는 영역"),
        ({"type": "GAZE", "slide_number": 9, "why": "-"}, "계획 · 대본에 없는 장"),
        ({"type": "FILLER", "slide_number": 2, "why": "-"}, "이번 미션 영역"),
    ],
)
def test_unsafe_relax_is_dropped_with_a_reason(relax, why):
    missions = [{"mission_id": "m1", "area": "FILLER", "slide_number": None}]
    resp = plan_coaching(request(missions=missions), llm=FakeLLM(draft(relax=[relax])), model="m")
    assert resp.coaching_plan.relax == []
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
        request(previous_take_result=OVERCOACHED),
        llm=FakeLLM(draft(focus, relax, max_interventions=1)),
        model="m",
    )
    assert [(f.area.value, f.weight) for f in resp.coaching_plan.focus] == [
        ("GAZE", 2.0),
        ("SPEED", 0.5),
        ("VOLUME", 1.3),
    ]
    assert resp.coaching_plan.relax == []
    assert resp.coaching_plan.max_interventions == 5
    assert len(resp.dropped) == 4


@pytest.mark.parametrize(
    ("review", "budget", "want", "why"),
    [
        (OVERCOACHED, 6, 6, None),
        (None, 6, None, "직전 Take 결과가 없어"),
        ({"interventions": "x"}, 6, None, "직전 Take 결과가 없어"),
        (take_result(4, effective=2), 2, None, "줄이지 못함"),
        (take_result(5), 3, None, "줄이지 못함"),  # 격려(CONTINUE)도 센 5번 = 하한 5
        (take_result(6), 3, 5, "5 으로 올림"),
    ],
    ids=[
        "fewer-than-before",
        "no-previous-take",
        "broken-take-result",
        "not-fewer-after-raising",
        "raised-to-the-floor-equals-before",
        "raised-to-the-floor-still-fewer",
    ],
)
def test_budget_only_when_it_says_less_than_last_take(review, budget, want, why):
    llm = FakeLLM(draft(max_interventions=budget))
    resp = plan_coaching(request(previous_take_result=review), llm=llm, model="m")
    assert resp.coaching_plan.max_interventions == want
    if why is not None:
        assert why in resp.dropped[-1]


def test_message_counts_numbers_per_slide():
    message = plan_message(PlanRequest.model_validate(request()), load_config().planner)
    counts = {s["slide_number"]: s["number_count"] for s in json.loads(message)["slides"]}
    assert counts[1] == 0 and counts[3] == 5  # 2024 · 3 · 12 · 4 · 18.2


class CodedError(Exception):
    """서비스 common/llm.py 처럼 fallback_reason 을 code 로 단 예외."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize(
    ("llm", "reason"),
    [
        (None, "LLM_UNAVAILABLE"),
        (FakeLLM(CodedError("LLM_TIMEOUT")), "LLM_TIMEOUT"),
        (FakeLLM(CodedError("LLM_INVALID_OUTPUT")), "LLM_INVALID_OUTPUT"),
        (FakeLLM(TimeoutError("slow")), "LLM_TIMEOUT"),
        (FakeLLM({"focus": "not a list"}), "LLM_INVALID_OUTPUT"),
        (FakeLLM(RuntimeError("boom")), "LLM_ERROR"),
        (FakeLLM(CodedError("NOT_A_REASON")), "LLM_ERROR"),
    ],
    ids=[
        "unavailable",
        "coded-timeout",
        "coded-invalid",
        "timeout",
        "invalid-output",
        "other",
        "bad-code",
    ],
)
def test_fallback_is_the_default_plan_with_a_reason(llm, reason):
    resp = plan_coaching(request(), llm=llm, model="m")
    assert resp.fallback_reason == reason
    assert resp.meta.model is None
    assert resp.coaching_plan == CoachingPlan()
    # 기본 계획을 coaching_plan 으로 싣고 하는 판단은 계획이 null 인 것과 같다
    plan = resp.coaching_plan.model_dump(mode="json")
    with_plan = decide_seen(make_request(20_000, coaching_plan=plan), _gaze_judges())
    without = decide_seen(make_request(20_000), _gaze_judges())
    assert with_plan[1] == without[1]


def test_broken_validation_falls_back_as_invalid_output(monkeypatch):
    def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("validate")

    monkeypatch.setattr("coach.planner.validate_draft", boom)
    resp = plan_coaching(request(), llm=FakeLLM(draft()), model="m")
    assert resp.fallback_reason == "LLM_INVALID_OUTPUT" and resp.meta.model is None


def test_response_echoes_the_basis_ids_and_ignores_old_fields():
    llm = FakeLLM(draft())
    old = {"take_id": "t9", "mode": "EXAM", "memory": {"recurring_issues": []}}
    resp = plan_coaching(
        request(source_take_id="take-122", script_version_id="sv-3", **old), llm=llm, model="m"
    )
    assert (resp.source_take_id, resp.script_version_id) == ("take-122", "sv-3")
    assert len(llm.calls) == 1  # 실전 모드라고 와도 모드가 없으므로 LLM 을 부른다
    assert plan_coaching(request(), llm=None).source_take_id is None


def test_cache_answers_the_same_question_without_calling_the_llm():
    cache = DictCache()
    llm = FakeLLM(draft(relax=[GAZE_3]))
    first = plan_coaching(request(), llm=llm, model="m", cache=cache)
    second = plan_coaching(request(), llm=llm, model="m", cache=cache)
    assert len(llm.calls) == 1
    assert first.coaching_plan == second.coaching_plan
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
    assert resp.fallback_reason is None and resp.meta.model == "m"
    assert len(llm.calls) == 1


def _message(**kw: Any) -> dict[str, Any]:
    req = PlanRequest.model_validate(request(**kw))
    return json.loads(plan_message(req, load_config().planner))


def test_message_carries_scripts_missions_and_recurring_issues():
    req = request(
        missions=[{"mission_id": "m1", "area": "TIME", "slide_number": 3, "description": "1분"}],
        recurring_issues=[{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
    )
    llm = FakeLLM(draft())
    plan_coaching(req, llm=llm, model="m")
    message = llm.calls[0][1][1]
    assert "18.2%" in message and '"type": "TIME"' in message
    assert message == plan_message(PlanRequest.model_validate(req), load_config().planner)
    body = json.loads(message)
    assert body["recurring_issues"] == [{"type": "GAZE", "slide_number": 3}]
    assert body["missions"][0]["description"] == "1분"
    assert all("required_keywords" not in s for s in body["slides"])
    assert body["previous_take"] is None and body["previous_review"] is None


def test_previous_take_is_counted_by_the_coach():
    result = take_result(
        6,
        effective=2,
        ineffective=3,  # 한 개는 못 잼, 격려(CONTINUE)도 개입 수에 든다
        gave_up=[
            {"issue_type": "GAZE_ON_SCRIPT", "slide_number": 2, "extra": 1},
            {"issue_type": "PACE_FAST", "slide_number": None},
        ],
        problem_segments=[
            {"area": "GAZE", "start_ms": 10_000, "end_ms": 22_500, "reliable": True},
            {"area": "GAZE", "start_ms": 30_000, "end_ms": 40_000, "reliable": True},
            {"area": "GAZE", "start_ms": 50_000, "end_ms": 90_000, "reliable": False},
            {"area": "SPEED", "start_ms": 0, "end_ms": 3_000, "reliable": True},
        ],
    )
    take = _message(previous_take_result=result)["previous_take"]
    assert take["interventions"] == 6
    assert take["effective_rate"] == 0.4  # 2 ÷ (2 + 3)
    assert take["gave_up"] == [
        {"issue_type": "GAZE_ON_SCRIPT", "slide_number": 2},
        {"issue_type": "PACE_FAST", "slide_number": None},
    ]
    assert take["problem_seconds"] == {"GAZE": 22.5, "SPEED": 3.0}  # 못 믿는 구간은 뺀다


def test_previous_take_without_measured_interventions_has_no_rate():
    unmeasured = take_result(3, effective=0, ineffective=0)
    take = _message(previous_take_result=unmeasured)["previous_take"]
    assert take["interventions"] == 3 and take["effective_rate"] is None


def test_previous_take_caps_gave_up_to_the_config_limit():
    limit = load_config().planner.max_previous_issues
    gave_up = [{"issue_type": "GAZE_ON_SCRIPT", "slide_number": i} for i in range(limit + 3)]
    take = _message(previous_take_result=take_result(1, gave_up=gave_up))["previous_take"]
    assert len(take["gave_up"]) == limit


def test_previous_review_carries_four_fields_and_trims_text():
    limit = load_config().planner.max_previous_issues
    review = {
        "overall_summary": "가" * 600,
        "dimensions": {"audience_gaze": "STABLE", "fluency": "PRIORITY"},
        "improvements": [{"area": "GAZE", "slide_number": 3, "text": "나" * 300, "evidence": {}}]
        * (limit + 2),
        "mission_results": [
            {"mission_id": f"m{i}", "status": "ACHIEVED", "metric": "x", "slide_number": None}
            for i in range(25)
        ],
        "score": 74,
    }
    got = _message(previous_review=review)["previous_review"]
    assert set(got) == {"overall_summary", "dimensions", "improvements", "mission_results"}
    assert len(got["overall_summary"]) == 500
    assert got["dimensions"] == {"audience_gaze": "STABLE", "fluency": "PRIORITY"}
    assert len(got["improvements"]) == limit
    assert got["improvements"][0] == {"type": "GAZE", "slide_number": 3, "text": "나" * 200}
    assert len(got["mission_results"]) == 20
    assert got["mission_results"][0] == {
        "mission_id": "m0",
        "status": "ACHIEVED",
        "metric": "x",
        "slide_number": None,
    }


def test_malformed_previous_items_are_skipped_without_raising():
    take = {
        "interventions": [None, "x", {"outcome": "EFFECTIVE"}, {"outcome": 5}],
        "gave_up": ["x", {"issue_type": 3}, {"issue_type": "PACE_FAST", "slide_number": "2"}],
        "problem_segments": [3, {"area": "GAZE", "start_ms": "a", "end_ms": 5, "reliable": True}],
    }
    review = {
        "overall_summary": 7,
        "dimensions": {"audience_gaze": 1, "fluency": "STABLE", 3: "x"},
        "improvements": ["x", {"area": 3}, {"area": "GAZE", "text": 9}],
        "mission_results": [None, {"mission_id": 1, "status": "ACHIEVED"}],
    }
    got = _message(previous_take_result=take, previous_review=review)
    assert got["previous_take"] == {
        "interventions": 4,
        "effective_rate": 1.0,
        "gave_up": [{"issue_type": "PACE_FAST", "slide_number": None}],
        "problem_seconds": {},
    }
    assert got["previous_review"] == {
        "overall_summary": None,
        "dimensions": {"fluency": "STABLE"},
        "improvements": [{"type": "GAZE", "slide_number": None, "text": None}],
        "mission_results": [
            {"mission_id": None, "status": "ACHIEVED", "metric": None, "slide_number": None}
        ],
    }
    # 최상위가 dict 가 아니어도 예외 없이 없는 것으로 본다
    empty = _message(previous_take_result={}, previous_review={})
    assert empty["previous_take"] is None and empty["previous_review"] is None
    plan_coaching(request(previous_take_result={"interventions": 5}), llm=FakeLLM(draft()))


def test_same_request_gives_the_same_message():
    kw = {"previous_take_result": take_result(4, effective=1), "previous_review": {"fluency": 1}}
    req = PlanRequest.model_validate(request(**kw))
    cfg = load_config().planner
    assert plan_message(req, cfg) == plan_message(req, cfg)


def test_relaxing_content_is_dropped():
    relax = {"type": "CONTENT", "slide_number": 3, "why": "-"}
    resp = plan_coaching(request(), llm=FakeLLM(draft(relax=[relax])), model="m")
    assert resp.coaching_plan.relax == []
    assert "봐줄 수 없는 영역" in resp.dropped[0]


def test_planner_hash_follows_the_prompt(monkeypatch):
    before = planner_hash("m")
    monkeypatch.setattr("coach.planner.PLAN_SYSTEM_PROMPT", "다른 지시문")
    assert planner_hash("m") != before
    assert planner_hash("m") != planner_hash("other")


def test_bad_segment_times_and_unknown_names_are_skipped():
    take = take_result(
        1,
        gave_up=[
            {"issue_type": "NOT_AN_ISSUE", "slide_number": 1},
            {"issue_type": "PACE_FAST", "slide_number": True},
            {"issue_type": "PACE_FAST", "slide_number": 2.5},
        ],
        problem_segments=[
            {"area": "GAZE", "start_ms": 0, "end_ms": 10**400, "reliable": True},
            {"area": "GAZE", "start_ms": -1, "end_ms": 5_000, "reliable": True},
            {"area": "GAZE", "start_ms": True, "end_ms": 5_000, "reliable": True},
            {"area": "GAZE", "start_ms": 0, "end_ms": 3_660_001, "reliable": True},
            {"area": "NOT_AN_AREA", "start_ms": 0, "end_ms": 5_000, "reliable": True},
            {"area": "TIME", "start_ms": 0, "end_ms": 3_660_000, "reliable": True},
        ],
    )
    got = _message(previous_take_result=take)["previous_take"]
    assert got["problem_seconds"] == {"TIME": 3660.0}
    assert got["gave_up"] == [
        {"issue_type": "PACE_FAST", "slide_number": None},
        {"issue_type": "PACE_FAST", "slide_number": None},
    ]


def test_review_values_outside_the_allowed_lists_are_skipped():
    review = {
        "dimensions": {
            "audience_gaze": "STABLE",
            "made_up": "STABLE",
            "fluency": "GREAT",
            "vocal_delivery": ["x"],
        },
        "improvements": [{"area": "WHATEVER", "text": "x"}, {"area": "TIME", "text": "y"}],
        "mission_results": [
            {"mission_id": "m" * 100, "status": "DONE", "metric": "k" * 100},
            {"mission_id": "m1", "status": "PARTIAL"},
        ],
    }
    got = _message(previous_review=review)["previous_review"]
    assert got["dimensions"] == {"audience_gaze": "STABLE"}
    assert [i["type"] for i in got["improvements"]] == ["TIME"]
    first, second = got["mission_results"]
    assert (first["mission_id"], first["metric"]) == ("m" * 64, "k" * 64)
    assert first["status"] is None and second["status"] == "PARTIAL"


def test_content_focus_is_dropped_before_the_count_limit():
    focus = [
        {"type": "CONTENT", "slide_number": None, "weight": 1.5, "why": "-"},
        {"type": "CONTENT", "slide_number": 3, "weight": 1.5, "why": "-"},
        {"type": "GAZE", "slide_number": None, "weight": 1.5, "why": "-"},
        {"type": "SPEED", "slide_number": None, "weight": 1.5, "why": "-"},
        {"type": "VOLUME", "slide_number": None, "weight": 1.5, "why": "-"},
    ]
    resp = plan_coaching(request(), llm=FakeLLM(draft(focus=focus)), model="m")
    assert [f.area.value for f in resp.coaching_plan.focus] == ["GAZE", "SPEED", "VOLUME"]
    assert len(resp.dropped) == 2
    assert all("발표 중 후보가 없음" in d and d.startswith("focus CONTENT") for d in resp.dropped)


def test_slides_known_only_from_scripts_count_as_plan_slides():
    scripts = [{"slide_number": 2, "script": "둘째 장 1개"}, {"slide_number": 1, "script": "첫 장"}]
    req = request(plan={}, scripts=scripts)
    slides = _message(plan={}, scripts=scripts)["slides"]
    assert [(s["slide_number"], s["target_seconds"]) for s in slides] == [(1, None), (2, None)]
    assert slides[1]["number_count"] == 1
    llm = FakeLLM(draft(relax=[{"type": "GAZE", "slide_number": 2, "why": "-"}]))
    resp = plan_coaching(req, llm=llm, model="m")
    assert [r.slide_number for r in resp.coaching_plan.relax] == [2] and resp.dropped == []
    # 시간 계획에만 있는 장도 싣는다
    only_plan = _message(scripts=[])["slides"]
    assert [s["slide_number"] for s in only_plan] == [1, 2, 3, 4]


def test_encouragement_counts_as_an_intervention_and_keeps_the_budget():
    ivs = [{"outcome": "EFFECTIVE", "instruction": "LOOK_AT_CAMERA"} for _ in range(5)]
    ivs.append({"outcome": None, "instruction": "CONTINUE"})
    result = {"interventions": ivs}
    assert _message(previous_take_result=result)["previous_take"]["interventions"] == 6
    resp = plan_coaching(
        request(previous_take_result=result), llm=FakeLLM(draft(max_interventions=5)), model="m"
    )
    assert resp.coaching_plan.max_interventions == 5  # 5 < 6 이라 유지


def test_planner_hash_follows_the_output_schema(monkeypatch):
    before = planner_hash("m")
    monkeypatch.setattr(
        PlanDraft, "model_json_schema", classmethod(lambda cls, *a, **k: {"changed": True})
    )
    assert planner_hash("m") != before


def test_script_is_cut_at_the_config_limit_and_why_at_200():
    long = "가" * 2_000
    cfg = load_config().planner
    message = plan_message(
        PlanRequest.model_validate(request(scripts=[{"slide_number": 1, "script": long}])), cfg
    )
    slide = next(s for s in json.loads(message)["slides"] if s["slide_number"] == 1)
    assert len(slide["script"]) == cfg.max_script_chars == 1_500
    focus = [{"type": "GAZE", "slide_number": None, "weight": 1.5, "why": "나" * 300}]
    relax = [{"type": "FILLER", "slide_number": 1, "why": "다" * 300}]
    resp = plan_coaching(request(), llm=FakeLLM(draft(focus, relax)), model="m")
    assert len(resp.coaching_plan.focus[0].why) == 200
    assert len(resp.coaching_plan.relax[0].why) == 200
