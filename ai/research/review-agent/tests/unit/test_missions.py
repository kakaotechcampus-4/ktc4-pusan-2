"""4단계 미션 판정 · 6단계 사용자 기준."""

from __future__ import annotations

import pytest

from review import load_config
from review.missions import judge_target

from .conftest import run, sample_coverage, sample_request

_REQ = sample_request()
TAKE = _REQ["take_result"]
CFG = load_config()


@pytest.mark.parametrize(
    ("metric", "operator", "value", "slide", "coverage", "expected"),
    [
        ("filler_per_min", "LTE", 2, None, None, ("PARTIAL", 2.4)),  # 0.4 ≤ 허용치 0.5
        ("filler_per_min", "LTE", 1, None, None, ("FAILED", 2.4)),
        ("filler_count", "LTE", 5, None, None, ("FAILED", 7)),  # 개수 지표는 허용치 없음
        ("audience_ratio", "GTE", 0.5, None, None, ("ACHIEVED", 0.58)),
        ("script_ratio", "LTE", 0.4, 3, None, ("FAILED", 0.62)),  # 장 값
        ("script_ratio", "LTE", 0.4, 1, None, ("NOT_EVALUABLE", None)),  # 그 장 값 없음
        ("content_coverage", "GTE", 0.9, None, None, ("NOT_EVALUABLE", None)),  # 대본 전달도 없음
        ("content_coverage", "GTE", 0.9, None, "coverage", ("ACHIEVED", 1.0)),
        ("content_coverage", "GTE", 0.9, 3, "coverage", ("ACHIEVED", 1.0)),
    ],
)
def test_judge_target(metric, operator, value, slide, coverage, expected):
    sc = sample_coverage() if coverage else None
    t = {"metric": metric, "operator": operator, "value": value}
    assert judge_target(t, slide, TAKE, sc, CFG) == expected


def test_failed_coverage_slide_is_not_evaluable():
    sc = sample_coverage()
    sc["failed"] = [{"slide_number": 2, "reason": "LLM_ERROR"}]
    t = {"metric": "content_coverage", "operator": "GTE", "value": 0.9}
    assert judge_target(t, 2, TAKE, sc, CFG) == ("NOT_EVALUABLE", None)


def test_sample_mission_and_criteria(req):
    resp, _ = run(req)
    m = resp.mission_results[0]
    assert (m.mission_id, m.status, m.observed, m.target) == ("m-1", "PARTIAL", 2.4, 2)
    c = resp.criteria_results[0]
    assert (c.display, c.status, c.observed, c.target) == ("추임새 5번 이하", "FAILED", 7, 5)


def test_criteria_without_number_are_skipped(req):
    req["user_criteria"]["targets"].append({"display": "자신감 있게"})
    resp, _ = run(req)
    assert len(resp.criteria_results) == 1
