"""1단계 판단 가능 여부 · 2단계 문제 부담."""

from __future__ import annotations

import pytest

from pitch_coach_ai.features.review import load_config

from .conftest import run, sample_coverage, segment, unmeasured


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
    req["take_result"]["areas"]["GAZE"] = unmeasured(0.3)
    resp, trace = run(req)
    assert not trace.evaluable["audience_gaze"]
    assert not trace.evaluable["script_independence"]
    assert resp.dimensions["script_independence"] == "NOT_EVALUABLE"
    assert trace.burden["script_independence"]["total"] == 0


def test_no_content_coverage_is_not_evaluable(req):
    # 채점할 핵심 내용이 없는 대본: 다 채점됐어도 강점이라 하지 않는다
    coverage = sample_coverage()
    coverage["take_scores"]["content_coverage"] = None
    req["script_coverage"] = coverage
    resp, trace = run(req)
    assert not trace.evaluable["content_delivery"]
    assert resp.dimensions["content_delivery"] == "NOT_EVALUABLE"


def test_scored_coverage_is_evaluable(req):
    req["script_coverage"] = sample_coverage()
    _, trace = run(req)
    assert trace.evaluable["content_delivery"]


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
    assert b["fluency"]["total"] == pytest.approx(6.6)  # 6.0 × 미션 m-1 PARTIAL 가중 1.1
    # reliable 이 아닌 VOLUME_LOW · 부담 제외 GAZE_ON_SCREEN 은 세지 않는다
    assert b["vocal_delivery"]["total"] == 0
    assert b["audience_gaze"]["total"] == 0


def test_script_used_drops_script_gaze(req):
    req["user_criteria"]["script_used"] = True
    resp, trace = run(req)
    assert trace.burden["script_independence"]["total"] == 0
    # 대본을 보기로 한 발표: 대본 의존을 문제로도 강점으로도 말하지 않는다
    assert not trace.evaluable["script_independence"]
    assert resp.dimensions["script_independence"] == "NOT_EVALUABLE"
    assert all("script_ratio" not in s.evidence for s in resp.strengths)


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

    # dimension 이 없으면 반복 판정에 쓰지 않는다 (가중 없음)
    req["previous_recurring_issues"] = [{"area": "GAZE", "slide_number": 3}]
    _, trace = run(req)
    assert trace.burden["audience_gaze"]["total"] == pytest.approx(5.0)
    assert trace.burden["script_independence"]["total"] == pytest.approx(13.08)


def test_time_mission_not_achieved_weights_pacing(req):
    # TIME 지표(slide_duration_ms) 미션도 pacing_rhythm 차원으로 가중된다
    req["take_result"]["problem_segments"].append(
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8)  # 12.0
    )
    req["missions"] = [
        {
            "mission_id": "m-time",
            "area": "TIME",
            "slide_number": 3,
            "target": {"metric": "slide_duration_ms", "operator": "LTE", "value": 50_000},
        }
    ]
    resp, trace = run(req, load_config(burden={"mission_failed_weight": 1.5}))
    assert resp.mission_results[0].status in ("FAILED", "PARTIAL")
    # 12.0 × 미션 가중 1.5 × gave_up(PACE_FAST) 가중 1.1
    assert trace.burden["pacing_rhythm"]["total"] == pytest.approx(19.8)


def test_pacing_partial_unmeasured_uses_measured_areas(req):
    # TIME 만 못 재면 pacing_rhythm 은 SPEED · PAUSE 로 판단하고, TIME 구간은 세지 않는다
    req["take_result"]["areas"]["TIME"] = unmeasured()
    req["take_result"]["problem_segments"] += [
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8),  # 세지 않음
        segment("PACE_FAST", "SPEED", 2, 30_000, 40_000, 0.5),  # 5.0 × gave_up 가중 1.1
    ]
    resp, trace = run(req)
    assert trace.evaluable["pacing_rhythm"]
    assert resp.dimensions["pacing_rhythm"] != "NOT_EVALUABLE"
    assert trace.burden["pacing_rhythm"]["total"] == pytest.approx(5.5)
    assert trace.burden["pacing_rhythm"]["by_area"] == {"SPEED": pytest.approx(5.5)}


def test_pacing_all_unmeasured_is_not_evaluable(req):
    for area in ("SPEED", "PAUSE", "TIME"):
        req["take_result"]["areas"][area] = unmeasured()
    resp, trace = run(req)
    assert not trace.evaluable["pacing_rhythm"]
    assert resp.dimensions["pacing_rhythm"] == "NOT_EVALUABLE"


def test_follows_coach_marking_not_ratio(req):
    # 리뷰는 measured_ratio 를 따로 비교하지 않는다: 코치가 표시하지 않았으면 잰 영역
    req["take_result"]["areas"]["GAZE"]["measured_ratio"] = 0.1
    _, trace = run(req)
    assert trace.evaluable["audience_gaze"]


def test_mission_in_unmeasured_area_is_not_evaluable(req):
    req["take_result"]["areas"]["FILLER"] = unmeasured(0.3)
    req["missions"] = [
        {
            "mission_id": "m-1",
            "area": "FILLER",
            "slide_number": None,
            "target": {"metric": "filler_per_min", "operator": "LTE", "value": 2},
        }
    ]
    resp, _ = run(req)
    assert resp.mission_results[0].status == "NOT_EVALUABLE"
