"""코칭 계획 — 저장할 행 · 바탕 값 · 계획 요청."""

import json
import uuid
from typing import Any

import pytest

from pitch_coach_backend.coach.client import PlanReply
from pitch_coach_backend.coach.requests import (
    PLAN_MAX_MISSION_DESCRIPTION,
    PLAN_MAX_MISSIONS,
    PLAN_MAX_SLIDES,
    MissionInput,
    PlanBasis,
    SlideTiming,
    build_plan,
    plan_basis,
    plan_for_evaluate,
    plan_payload,
    plan_request,
    request_basis,
)

# ── 코칭 계획 ─────────────────────────────────────────────────────────


PLAN_REPLY = PlanReply.model_validate(
    {
        "coaching_plan": {
            "focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5, "why": "근거"}],
            "relax": [{"area": "FILLER", "slide_number": 1, "why": "근거"}],
            "max_interventions": 4,
        },
        "source_take_id": "take-122",
        "script_version_id": "sv-3",
        "fallback_reason": None,
        "dropped": ["relax TIME 3: 봐줄 수 없는 영역"],
        "planner_hash": "0123456789ab",
        "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": "m"},
    }
)
PITCH_PLAN = build_plan(300, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=30)
PLAN_REQUEST = plan_request(
    pitch_id="p-1",
    source_take_id="take-122",
    script_version_id="sv-3",
    plan=PITCH_PLAN,
    scripts=[(1, "첫 장")],
)
NOW = {
    "source_take_id": "take-122",
    "script_version_id": "sv-3",
    "time_limit_sec": 300,
    "lower_deviation_sec": 30,
    "upper_deviation_sec": 30,
}


def test_plan_row_keeps_the_reply_and_what_it_was_asked_for():
    assert PLAN_REQUEST is not None

    payload = plan_payload(PLAN_REQUEST, PLAN_REPLY)
    basis = request_basis(PLAN_REQUEST)

    assert payload == {
        "coaching_plan": PLAN_REPLY.coaching_plan,
        "fallback_reason": None,
        "dropped": ["relax TIME 3: 봐줄 수 없는 영역"],
        "planner_hash": "0123456789ab",
    }
    assert basis == PlanBasis("take-122", "sv-3", 300_000, 270_000, 330_000)
    # JSONB 에 넣을 수 있어야 한다
    assert json.loads(json.dumps(payload)) == payload


def test_plan_basis_comes_from_the_request_even_if_the_reply_drops_the_ids():
    assert PLAN_REQUEST is not None
    reply = PLAN_REPLY.model_copy(update={"source_take_id": None, "script_version_id": None})

    plan_payload(PLAN_REQUEST, reply)

    assert request_basis(PLAN_REQUEST) == plan_basis(**NOW)


def test_plan_is_sent_without_why():
    assert PLAN_REQUEST is not None
    payload = plan_payload(PLAN_REQUEST, PLAN_REPLY)

    sent = plan_for_evaluate(payload)

    assert sent == {
        "focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5}],
        "relax": [{"area": "FILLER", "slide_number": 1}],
        "max_interventions": 4,
    }
    # 저장된 값은 그대로다
    assert payload["coaching_plan"]["focus"][0]["why"] == "근거"


@pytest.mark.parametrize("payload", [None, {}, {"coaching_plan": None}, {"coaching_plan": []}])
def test_broken_plan_row_is_not_sent(payload: Any):
    assert plan_for_evaluate(payload) is None


def test_plan_ids_compare_as_strings():
    """DB 의 Uuid 칼럼은 uuid.UUID 로, JSON 으로 오간 id 는 문자열로 읽힌다."""
    take_id, script_id, pitch_id = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    request = plan_request(
        pitch_id=pitch_id,
        source_take_id=take_id,
        script_version_id=script_id,
        plan=PITCH_PLAN,
        scripts=[],
    )
    assert request is not None
    request = json.loads(json.dumps(request))

    now = plan_basis(**{**NOW, "source_take_id": take_id, "script_version_id": script_id})

    assert request["pitch_id"] == str(pitch_id)
    assert request_basis(request) == now


@pytest.mark.parametrize(
    "now",
    [
        {"source_take_id": "take-123"},
        {"source_take_id": None},
        {"script_version_id": "sv-4"},
        {"time_limit_sec": 360},
        {"lower_deviation_sec": 0},
        {"upper_deviation_sec": 0},
    ],
)
def test_plan_basis_differs_when_anything_changed(now: dict[str, Any]):
    assert PLAN_REQUEST is not None

    assert request_basis(PLAN_REQUEST) != plan_basis(**{**NOW, **now})


def test_plan_basis_with_no_slack_keeps_null_min():
    """허용 미달이 제한 시간 이상이면 min_ms 는 null 이다. null 끼리는 같은 값이다 (12-2)."""
    basis = plan_basis(
        source_take_id=None,
        script_version_id=None,
        time_limit_sec=60,
        lower_deviation_sec=60,
        upper_deviation_sec=0,
    )

    assert basis == PlanBasis(None, None, 60_000, None, 60_000)


def test_plan_request_has_the_contract_fields():
    body = plan_request(
        pitch_id="p-1",
        source_take_id=None,
        script_version_id="sv-3",
        plan=PITCH_PLAN,
        scripts=[(2, "둘째 장"), (1, "첫 장")],
        missions=[MissionInput("m-1", "GAZE", None, None)],
        recurring_issues=[{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
    )

    assert body is not None
    # coach-agent.md 5-2 plan 표
    assert list(body) == [
        "pitch_id",
        "source_take_id",
        "script_version_id",
        "plan",
        "scripts",
        "missions",
        "recurring_issues",
        "previous_take_result",
        "previous_review",
    ]
    assert body["scripts"] == [
        {"slide_number": 1, "script": "첫 장"},
        {"slide_number": 2, "script": "둘째 장"},
    ]
    assert body["recurring_issues"][0]["dimension"] == "script_independence"


def test_plan_request_over_the_input_limit_is_not_made():
    many_slides = [SlideTiming(n, 1_000, 10) for n in range(1, PLAN_MAX_SLIDES + 2)]
    big_plan = build_plan(
        300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0, slides=many_slides
    )
    common: dict[str, Any] = {"pitch_id": "p", "source_take_id": None, "script_version_id": None}

    assert plan_request(**common, plan=big_plan, scripts=[]) is None
    assert (
        plan_request(
            **common,
            plan=PITCH_PLAN,
            scripts=[],
            missions=[MissionInput(f"m-{i}", "GAZE", None, None) for i in range(21)],
        )
        is None
    )
    assert PLAN_MAX_MISSIONS == 20
    assert (
        plan_request(
            **common,
            plan=PITCH_PLAN,
            scripts=[],
            missions=[MissionInput(f"m-{i}", "GAZE", None, None) for i in range(20)],
        )
        is not None
    )


def test_plan_request_drops_only_a_too_long_description():
    long = "가" * (PLAN_MAX_MISSION_DESCRIPTION + 1)
    exact = "나" * PLAN_MAX_MISSION_DESCRIPTION

    body = plan_request(
        pitch_id="p",
        source_take_id=None,
        script_version_id=None,
        plan=PITCH_PLAN,
        scripts=[],
        missions=[
            MissionInput("m-1", "GAZE", 3, None, priority=2, description=long),
            MissionInput("m-2", "GAZE", 3, None, description=exact),
        ],
    )

    assert body is not None
    assert body["missions"][0] == {
        "mission_id": "m-1",
        "area": "GAZE",
        "slide_number": 3,
        "target": None,
        "priority": 2,
    }
    assert body["missions"][1]["description"] == exact
