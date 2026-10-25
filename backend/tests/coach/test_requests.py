"""코치 요청 만들기 — 시간 계획 · Take 상수 · 요청 모양.

AI 예시 요청(`ai/research/coach-agent/src/coach/examples/`)과 키가 같은지 본다.
"""

import json
import uuid
from typing import Any

import pytest

from pitch_coach_backend.coach.requests import (
    MissionInput,
    SlideTiming,
    TakeConstants,
    build_plan,
    evaluate_request,
    finalize_request,
    missions_payload,
)
from tests.coach.conftest import example

SLIDES = [
    SlideTiming(slide_number=2, expected_ms=60_000, script_chars=300),
    SlideTiming(slide_number=1, expected_ms=60_000, script_chars=None),
]


def constants(**overrides: Any) -> TakeConstants:
    values: dict[str, Any] = {
        "take_id": "take-1",
        "mode": "COACHING",
        "plan": build_plan(300, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=30),
        "script_mode": "HIGHLIGHT",
    }
    return TakeConstants(**{**values, **overrides})


# ── 시간 계획 ─────────────────────────────────────────────────────────


def test_plan_target_is_the_goal_and_range_is_the_time_limit():
    plan = build_plan(
        240, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=60, slides=SLIDES
    )

    assert plan == {
        "target_ms": 240_000,
        "min_ms": 270_000,
        "max_ms": 360_000,
        "slides": [
            {"slide_number": 1, "target_ms": 60_000, "script_chars": 0},
            {"slide_number": 2, "target_ms": 60_000, "script_chars": 300},
        ],
    }


def test_plan_min_is_null_when_the_range_reaches_zero():
    plan = build_plan(60, time_limit_sec=60, lower_deviation_sec=60, upper_deviation_sec=0)

    assert (plan["min_ms"], plan["max_ms"]) == (None, 60_000)


def test_zero_deviation_means_no_slack_not_unknown():
    plan = build_plan(300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0)

    assert (plan["min_ms"], plan["max_ms"]) == (300_000, 300_000)


def test_slides_are_left_out_unless_every_slide_has_a_target():
    partial = [*SLIDES, SlideTiming(slide_number=3, expected_ms=None, script_chars=100)]

    plan = build_plan(
        300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0, slides=partial
    )

    assert plan["slides"] == []


def test_slide_targets_follow_the_current_goal_when_the_parse_used_another():
    """제한 시간만 바뀌면 다시 파싱하지 않는다. 장 목표를 지금 목표에 맞춰 비율로 바꾼다
    (script-parser.md 12-2). 누적으로 반올림해 합이 목표와 같다."""
    parsed = [
        SlideTiming(slide_number=1, expected_ms=100_000, script_chars=500),
        SlideTiming(slide_number=2, expected_ms=100_000, script_chars=500),
        SlideTiming(slide_number=3, expected_ms=100_000, script_chars=500),
    ]

    plan = build_plan(
        200,
        time_limit_sec=200,
        lower_deviation_sec=0,
        upper_deviation_sec=0,
        slides=parsed,
        parsed_target_ms=300_000,
    )

    targets = [s["target_ms"] for s in plan["slides"]]
    # 200000 × 1/3 = 66666.67 → 66667, 누적 133333.33 → 133333, 마지막은 남은 몫
    assert targets == [66_667, 66_666, 66_667]
    assert sum(targets) == 200_000


def test_slide_targets_are_kept_without_a_parse_target():
    """파싱 때 목표가 없었으면(기준 말 속도로 잼) 비율로 바꿀 기준이 없다. 그대로 싣는다."""
    plan = build_plan(
        200,
        time_limit_sec=200,
        lower_deviation_sec=0,
        upper_deviation_sec=0,
        slides=SLIDES,
        parsed_target_ms=None,
    )

    assert [s["target_ms"] for s in plan["slides"]] == [60_000, 60_000]


# ── Take 상수 ─────────────────────────────────────────────────────────


def test_missions_leave_out_missing_priority_and_description():
    payload = missions_payload(
        [
            MissionInput(
                "m-1", "GAZE", 3, {"metric": "script_ratio", "operator": "LTE", "value": 0.4}
            ),
            MissionInput("m-2", "FILLER", None, None, priority=2, description="덜 말하기"),
        ]
    )

    assert payload == [
        {
            "mission_id": "m-1",
            "area": "GAZE",
            "slide_number": 3,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.4},
        },
        {
            "mission_id": "m-2",
            "area": "FILLER",
            "slide_number": None,
            "target": None,
            "priority": 2,
            "description": "덜 말하기",
        },
    ]


@pytest.mark.parametrize(
    ("mode", "sent"),
    [("COACHING", "COACHING"), ("EXAM", "EXAM"), ("exam", "EXAM"), ("PRACTICE", "COACHING")],
)
def test_mode_outside_the_contract_falls_back_to_coaching(mode: str, sent: str):
    assert constants(mode=mode).common()["mode"] == sent


@pytest.mark.parametrize(
    ("script_mode", "sent"),
    [("KEYWORD", "KEYWORD"), ("off", "OFF"), ("FULL", None), (None, None)],
)
def test_script_mode_outside_the_contract_is_not_sent(script_mode: str | None, sent: str | None):
    request = finalize_request(constants(script_mode=script_mode), 1000, {}, None, [])

    assert request["script_mode"] == sent


def test_broken_base_level_is_not_sent():
    assert constants(base_level_db=float("nan")).common()["calibration"] == {"base_level_db": None}
    assert constants(base_level_db=-29.5).common()["calibration"] == {"base_level_db": -29.5}


# ── 요청 모양 ─────────────────────────────────────────────────────────


def test_evaluate_request_has_the_example_keys():
    body = evaluate_request(constants(), 92_000, {}, None)

    assert body.keys() == example("evaluate/request.json").keys()
    assert (body["t_ms"], body["coach_state"]) == (92_000, None)


def test_finalize_request_has_the_example_keys():
    events = example("finalize/request.json")["events"]

    body = finalize_request(constants(), 92_000, {}, {"v": 2}, events)
    replayed = finalize_request(constants(), 92_000, {}, None, events, replay={"words": []})

    assert body.keys() == example("finalize/request.json").keys()
    assert replayed.keys() == example("finalize/request_replay.json").keys()
    assert body["events"] == events


def test_example_request_round_trips_through_take_constants():
    """예시 요청의 Take 상수를 TakeConstants 에 넣으면 같은 값이 다시 나온다."""
    body = example("evaluate/request.json")
    take = TakeConstants(
        take_id=body["take_id"],
        mode=body["mode"],
        plan=body["plan"],
        missions=body["missions"],
        recurring_issues=body["recurring_issues"],
        coaching_plan=body["coaching_plan"],
        script_used=body["script_used"],
        base_level_db=body["calibration"]["base_level_db"],
    )

    rebuilt = evaluate_request(take, body["t_ms"], body["inputs"], body["coach_state"])

    assert rebuilt == body


def test_finalize_request_copies_the_events():
    events = [{"event_id": "e", "kind": "SLIDE", "t_ms": 1}]

    body = finalize_request(constants(), 1000, {}, None, events)
    body["events"][0]["t_ms"] = 999

    assert events[0]["t_ms"] == 1


def test_take_id_is_sent_as_a_string():
    """realtime 은 Take id 를 uuid.UUID 로 들고 있다. 그대로 실으면 JSON 으로 못 바꾼다."""
    take_id = uuid.uuid7()

    body = evaluate_request(constants(take_id=take_id), 1000, {}, None)

    assert body["take_id"] == str(take_id)
    json.dumps(body)
