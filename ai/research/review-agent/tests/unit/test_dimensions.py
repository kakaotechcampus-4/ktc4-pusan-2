"""3단계 차원 상태."""

from __future__ import annotations

from review import load_config

from .conftest import run, segment


def test_sample_states(req):
    resp, _ = run(req)
    assert resp.dimensions == {
        "audience_gaze": "STRENGTH",
        "script_independence": "PRIORITY",
        "pacing_rhythm": "STRENGTH",
        "vocal_delivery": "STRENGTH",
        "fluency": "STABLE",
        "content_delivery": "NOT_EVALUABLE",
    }


def test_old_recurring_format_marks_both_gaze_dimensions(req):
    """dimension 이 없는 예전 반복 문제는 GAZE 두 차원을 다 반복 문제로 본다 (알려진 한계)"""
    req["previous_recurring_issues"] = [{"area": "GAZE", "slide_number": 3}]
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "IMPROVED"


def test_recurring_resolved_is_improved(req):
    req["take_result"]["problem_segments"] = [
        s for s in req["take_result"]["problem_segments"] if s["issue_type"] != "GAZE_ON_SCRIPT"
    ]
    resp, _ = run(req)
    assert resp.dimensions["script_independence"] == "IMPROVED"


def test_recurring_cut_by_priority_max_is_stable(req):
    """부담은 기준 이상인데 개수 상한에서 밀린 반복 문제는 좋아진 것이 아니다"""
    req["take_result"]["problem_segments"].append(
        segment("FILLER_FREQUENT", "FILLER", 1, 0, 20_000, 0.6)
    )
    resp, trace = run(req, load_config(dimension={"priority_max_count": 1}))
    assert trace.burden["fluency"]["total"] > trace.burden["script_independence"]["total"] >= 10
    assert resp.dimensions["fluency"] == "PRIORITY"
    assert resp.dimensions["script_independence"] == "STABLE"


def test_achieved_mission_without_problem_is_improved(req):
    req["missions"] = [
        {
            "mission_id": "m-2",
            "area": "VOLUME",
            "slide_number": None,
            "target": {"metric": "voice_diff_db", "operator": "GTE", "value": -3},
        }
    ]
    resp, _ = run(req)
    assert resp.mission_results[0].status == "ACHIEVED"
    assert resp.dimensions["vocal_delivery"] == "IMPROVED"


def test_priority_ties_break_by_name(req):
    req["previous_recurring_issues"] = []
    req["take_result"]["problem_segments"] = [
        segment("FILLER_FREQUENT", "FILLER", 1, 0, 20_000, 0.6),
        segment("VOLUME_LOW", "VOLUME", 2, 0, 20_000, 0.6),
    ]
    resp, _ = run(req, load_config(dimension={"priority_max_count": 1}))
    # 부담이 같으면 이름순: fluency < vocal_delivery
    assert resp.dimensions["fluency"] == "PRIORITY"
    assert resp.dimensions["vocal_delivery"] == "STABLE"
