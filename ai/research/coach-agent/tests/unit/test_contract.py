"""계약 — BE 가 보내고 받는 모양. 여기가 깨지면 BE · FE · 리뷰가 깨진다."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from coach import decide, plan_coaching
from coach.config import load_config
from coach.schemas import CoachRequest, CoachResponse, Mission, PlanRequest
from coach.state import CoachState, dump_state, load_state
from coach.version import FEATURE_VERSION, SCHEMA_VERSION, STATE_VERSION
from coach.vocab import FeedbackType, Instruction

from .conftest import make_request
from .fakes import fake_judges


def test_first_request_without_state_starts_fresh():
    resp = decide(make_request(1000), fake_judges())
    assert resp.meta.schema_version == SCHEMA_VERSION
    assert resp.coach_state["v"] == STATE_VERSION
    assert resp.action.value == "WAIT"
    assert resp.reason_codes == ["NO_CANDIDATE"]
    assert resp.feedback is None


def test_response_has_exactly_the_contract_fields():
    cfg = load_config()
    resp = decide(make_request(1000), fake_judges(), cfg)
    blob = resp.model_dump(mode="json")
    assert set(blob) == {
        "action",
        "feedback",
        "indicators",
        "reason_codes",
        "events",
        "coach_state",
        "meta",
    }
    assert set(blob["meta"]) == {"schema_version", "feature_version", "criteria_versions", "model"}
    meta = resp.meta
    assert meta.schema_version == SCHEMA_VERSION == "1.0"
    assert meta.feature_version == FEATURE_VERSION == "coach-1.2"
    assert meta.model is None
    assert set(meta.criteria_versions) == {"gaze", "pace", "volume", "filler", "timing", "coach"}
    assert meta.criteria_versions["coach"] == f"coach-1.2+{cfg.config_hash()}"


def test_stale_tick_response_has_meta_too():
    judges = fake_judges()
    first = decide(make_request(5000), judges)
    again = decide(make_request(5000, state=first.coach_state), judges)
    assert again.meta == first.meta


def test_response_is_plain_json_round_trip():
    resp = decide(make_request(1000), fake_judges())
    blob = json.dumps(resp.model_dump(mode="json"), ensure_ascii=False)
    again = CoachResponse.model_validate_json(blob)
    assert again == resp


def test_unknown_request_fields_are_ignored():
    req = make_request(1000)
    req["something_new_from_be"] = {"x": 1}
    req["inputs"]["voice_records"][0]["new_meter"] = 3
    decide(req, fake_judges())  # 깨지지 않는다


def test_missing_areas_skip_only_that_area():
    req = make_request(1000, slide=None)
    req["inputs"] = {}
    resp = decide(req, fake_judges())
    assert resp.action.value == "WAIT"


def test_negative_time_is_rejected():
    with pytest.raises(ValidationError):
        CoachRequest.model_validate(make_request(-1))


def test_state_round_trips_without_loss():
    state, reset = load_state(None)
    assert not reset
    state.history = []
    state.slide_totals = {"3": {"SPEED": {"chars": 150}}}
    raw = dump_state(state)
    again, reset = load_state(json.loads(json.dumps(raw)))
    assert not reset
    assert again == state


def test_state_dump_keeps_version_even_though_it_is_default():
    assert dump_state(CoachState())["v"] == STATE_VERSION


def test_foreign_state_is_reset_and_reported():
    resp = decide(make_request(1000, state={"v": 999, "whatever": 1}), fake_judges())
    assert "STATE_RESET" in resp.reason_codes
    resp = decide(
        make_request(1000, state={"v": STATE_VERSION, "history": "broken"}), fake_judges()
    )
    assert "STATE_RESET" in resp.reason_codes


def test_old_tick_does_not_change_state():
    judges = fake_judges()
    first = decide(make_request(5000), judges)
    again = decide(make_request(5000, state=first.coach_state), judges)
    assert again.reason_codes == ["STALE_TICK"]
    assert again.coach_state == first.coach_state


def test_feedback_type_enum_matches_review_and_mission_types():
    # 리뷰의 ReviewPoint.type · Mission.area 과 같은 7개
    assert {t.value for t in FeedbackType} == {
        "GAZE",
        "SPEED",
        "VOLUME",
        "PAUSE",
        "FILLER",
        "CONTENT",
        "TIME",
    }


def test_every_ladder_step_has_a_template():
    cfg = load_config()
    for issue, rule in cfg.issues.items():
        for step in rule.ladder:
            key = f"{step.instruction.value}.{step.variant}"
            assert key in cfg.templates, f"{issue}: {key} 문구가 없습니다"
    assert {s.instruction for r in cfg.issues.values() for s in r.ladder} == set(Instruction)


def test_config_override_changes_hash():
    base = load_config()
    tuned = load_config(policy={"cooldown_ms": 20_000})
    assert tuned.policy.cooldown_ms == 20_000
    assert tuned.policy.min_gap_ms == base.policy.min_gap_ms  # 나머지는 기본값
    assert tuned.config_hash() != base.config_hash()


# ── /coach/plan ─────────────────────────────────────────────────────────────

#: coach-agent.md 5-5 plan 요청 예시 (previous_* 는 null)
PLAN_REQUEST_EXAMPLE = {
    "pitch_id": "p-1",
    "source_take_id": "take-122",
    "script_version_id": "sv-3",
    "plan": {
        "target_ms": 300000,
        "min_ms": 270000,
        "max_ms": 330000,
        "slides": [
            {"slide_number": 1, "target_ms": 60000, "script_chars": 300},
            {"slide_number": 2, "target_ms": 60000, "script_chars": 300},
            {"slide_number": 3, "target_ms": 90000, "script_chars": 450},
        ],
    },
    "scripts": [
        {
            "slide_number": 3,
            "script": "지난 분기 매출은 37% 늘었습니다. 신규 고객이 늘어난 덕분입니다.",
        }
    ],
    "missions": [
        {
            "mission_id": "m-2",
            "area": "GAZE",
            "slide_number": 3,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.4},
        }
    ],
    "recurring_issues": [{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
    "previous_take_result": None,
    "previous_review": None,
}


class _Draft:
    """초안이 비어 있는 LLM 흉내."""

    def invoke(self, messages):
        return {"focus": [], "relax": [], "max_interventions": None}


def test_plan_response_has_exactly_the_contract_fields():
    resp = plan_coaching(PLAN_REQUEST_EXAMPLE, llm=_Draft(), model="m")
    blob = resp.model_dump(mode="json")
    assert set(blob) == {
        "coaching_plan",
        "source_take_id",
        "script_version_id",
        "fallback_reason",
        "dropped",
        "planner_hash",
        "meta",
    }
    assert set(blob["meta"]) == {"schema_version", "feature_version", "model"}
    assert set(blob["coaching_plan"]) == {"focus", "relax", "max_interventions"}
    assert blob["meta"] == {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model": "m",
    }
    assert (blob["source_take_id"], blob["script_version_id"]) == ("take-122", "sv-3")


def test_plan_request_needs_pitch_id():
    body = {k: v for k, v in PLAN_REQUEST_EXAMPLE.items() if k != "pitch_id"}
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(body)


def _slides(n: int) -> list[dict]:
    return [{"slide_number": i, "target_ms": 1000} for i in range(1, n + 1)]


@pytest.mark.parametrize(
    ("field", "make"),
    [
        ("plan", lambda n: {"slides": _slides(n)}),
        ("scripts", lambda n: [{"slide_number": i, "script": "a"} for i in range(n)]),
        ("missions", lambda n: [{"mission_id": f"m{i}", "area": "GAZE"} for i in range(n)]),
        ("recurring_issues", lambda n: [{"area": "GAZE"} for _ in range(n)]),
    ],
    ids=["plan.slides", "scripts", "missions", "recurring_issues"],
)
def test_plan_request_array_limits(field, make):
    limit = {"plan": 100, "scripts": 100, "missions": 20, "recurring_issues": 20}[field]
    PlanRequest.model_validate({"pitch_id": "p", field: make(limit)})
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"pitch_id": "p", field: make(limit + 1)})


@pytest.mark.parametrize(
    ("description", "ok"),
    [("가" * 500, True), ("가" * 501, False), (None, True), ("", True)],
    ids=["exactly-500", "501", "null", "empty"],
)
def test_plan_mission_description_limit_counts_code_points(description, ok):
    mission = {"mission_id": "m", "area": "GAZE", "description": description}
    body = {"pitch_id": "p", "missions": [mission]}
    if ok:
        PlanRequest.model_validate(body)
    else:
        with pytest.raises(ValidationError):
            PlanRequest.model_validate(body)
    # 설명을 아예 빼도 통과한다
    PlanRequest.model_validate({"pitch_id": "p", "missions": [{"mission_id": "m", "area": "GAZE"}]})


def test_evaluate_mission_description_is_not_limited():
    long = "가" * 501
    Mission.model_validate({"mission_id": "m", "area": "GAZE", "description": long})
    mission = {"mission_id": "m", "area": "GAZE", "description": long}
    req = CoachRequest.model_validate({"take_id": "t", "t_ms": 1000, "missions": [mission]})
    assert req.missions[0].description == long


def test_evaluate_ignores_source_in_the_coaching_plan():
    body = {"take_id": "t", "t_ms": 1000, "coaching_plan": {"source": "LLM", "focus": []}}
    req = CoachRequest.model_validate(body)
    assert req.coaching_plan is not None and not hasattr(req.coaching_plan, "source")
    plan = {"source": "LLM", "focus": [], "relax": []}
    decide(make_request(1000, coaching_plan=plan), fake_judges())
