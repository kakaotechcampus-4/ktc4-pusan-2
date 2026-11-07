"""가상 기록이 실제 저장 payload 모양과 같은지 (연구 전용 fixtures, 기능 문서 12-3 '끝까지 한 발표')."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from chat_agent_lab.service_tests import fixtures as fx

COACH = json.loads(
    (
        resources.files("pitch_coach_ai.features.coach") / "examples" / "finalize" / "response.json"
    ).read_text(encoding="utf-8")
)["take_result"]


def test_take_result_field_sets_match_coach_example() -> None:
    ours = fx.take_result(take_id="t-1")
    assert set(ours) == set(COACH)
    for area, data in COACH["areas"].items():
        assert set(ours["areas"][area]["take"]) == set(data["take"]), area
        assert set(ours["areas"][area]["slides"][0]["metrics"]) == set(
            data["slides"][0]["metrics"]
        ), area
        assert set(ours["areas"][area]["slides"][0]) == set(data["slides"][0]), area
    assert set(ours["criteria"]) == set(COACH["criteria"])


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({}, True),
        ({"visited_slides": [1, 2, 4]}, None),  # 장 기록 일부 누락 → 판단 불가
        ({"end_reason": "SILENCE"}, None),  # 자동 종료 → 판단 불가
        ({"end_reason": "TIME_LIMIT"}, None),
        ({"end_reason": "ABANDONED"}, False),  # 중단 → false
        ({"status": "CLOSED"}, False),
        ({"page_count": None}, None),
        ({"page_count": 0}, None),
        ({"visited_slides": [1, 2, 3, 4, 4, 2]}, True),  # 중복 방문은 상관없음
    ],
)
def test_completed_fully_rule(kw, expected) -> None:
    t = fx.TakeRecord(take_id="t-1", take_number=1, **kw)
    assert fx.completed_fully(t) is expected


def test_take_reviewed_example_is_consistent() -> None:
    from chat_agent_lab import examples

    files = examples.build()
    req = files["request_take_reviewed.json"]
    final = files["final_take_reviewed.json"]
    assert final["diagnostics"]["run_id"] == req["run"]["run_id"]
    goal = req["context"]["conversation_goal"]
    t6 = next(t for t in req["context"]["pitch"]["takes"] if t["take_id"] == "t-6")
    assert t6["completed_at"] > goal["created_at"]
    assert final["basis"]["conversation_goal_version"] == goal["version"]
