"""1단계 판단 가능 여부 · 2단계 문제 부담."""

from __future__ import annotations

import pytest

from .conftest import run, sample_coverage, segment


def test_sample_evaluable(req):
    _, trace = run(req)
    assert trace.evaluable == {
        "audience_gaze": True,
        "script_independence": True,
        "pacing_rhythm": True,
        "vocal_delivery": True,
        "fluency": True,
        "content_delivery": False,  # 대본 전달도 없음
    }


def test_unmeasured_area_is_not_evaluable(req):
    req["take_result"]["areas"]["GAZE"] = {"measured_ratio": 0.3, "take": None, "slides": None}
    resp, trace = run(req)
    assert not trace.evaluable["audience_gaze"]
    assert not trace.evaluable["script_independence"]
    assert resp.dimensions["script_independence"] == "NOT_EVALUABLE"
    assert trace.burden["script_independence"]["total"] == 0


def test_low_scored_ratio_is_not_evaluable(req):
    coverage = sample_coverage()
    coverage["take_scores"]["scored_ratio"] = 0.3
    req["script_coverage"] = coverage
    _, trace = run(req)
    assert not trace.evaluable["content_delivery"]


def test_sample_burden(req):
    _, trace = run(req)
    b = trace.burden
    # (0.71 × 13 + 0.55 × 7) × 반복 가중 1.2
    assert b["script_independence"]["total"] == pytest.approx(15.696)
    assert b["script_independence"]["by_slide"] == {3: pytest.approx(15.696)}
    assert b["script_independence"]["by_area"] == {"GAZE": pytest.approx(15.696)}
    assert b["fluency"]["total"] == pytest.approx(6.0)
    # reliable 이 아닌 VOLUME_LOW · 부담 제외 GAZE_ON_SCREEN 은 세지 않는다
    assert b["vocal_delivery"]["total"] == 0
    assert b["audience_gaze"]["total"] == 0


def test_script_used_drops_script_gaze(req):
    req["user_criteria"]["script_used"] = True
    _, trace = run(req)
    assert trace.burden["script_independence"]["total"] == 0


def test_content_burden_counts_missing_and_contradicted_key_points(req):
    coverage = sample_coverage()
    coverage["evaluations"][0]["key_points"] += [
        {"key_point_id": "K2", "importance": "high", "status": "missing"},  # 6
        {"key_point_id": "K3", "importance": "normal", "status": "contradicted"},  # 3
        {"key_point_id": "K4", "importance": "critical", "status": "partial"},  # 세지 않음
        {"key_point_id": "K5", "status": "missing"},  # importance 없음 → normal 3
        {"key_point_id": "K6", "importance": "weird", "status": "missing"},  # 모르는 값 → normal 3
    ]
    coverage["evaluations"][0]["critical_facts"].append(
        {"fact_id": "CF2", "value": "10", "status": "mismatched"}  # 수치는 세지 않음
    )
    req["script_coverage"] = coverage
    _, trace = run(req)
    assert trace.burden["content_delivery"]["total"] == pytest.approx(15.0)
    assert trace.burden["content_delivery"]["by_slide"] == {3: pytest.approx(15.0)}


def test_recurring_weight_follows_dimension(req):
    # 새 형식: dimension 이 있으면 그 차원만 가중
    req["take_result"]["problem_segments"].append(segment("GAZE_AWAY", "GAZE", 1, 0, 10_000, 0.5))
    _, trace = run(req)
    assert trace.burden["audience_gaze"]["total"] == pytest.approx(5.0)

    # 예전 형식: area 만 있으면 GAZE 의 두 차원 모두 가중
    req["previous_recurring_issues"] = [{"area": "GAZE", "slide_number": 3}]
    _, trace = run(req)
    assert trace.burden["audience_gaze"]["total"] == pytest.approx(6.0)
    assert trace.burden["script_independence"]["total"] == pytest.approx(15.696)
