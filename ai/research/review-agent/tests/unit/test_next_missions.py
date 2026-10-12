"""7단계 다음 미션."""

from __future__ import annotations

from .conftest import run, sample_coverage, segment, target


def descriptions(missions) -> list[str]:
    return [m.description if hasattr(m, "description") else m["description"] for m in missions]


def test_sample_next_missions(req):
    resp, _ = run(req)
    assert descriptions(resp.next_missions) == ["추임새 5번 이하", "3번 장 대본 응시 비율 52% 이하"]
    first, second = resp.next_missions
    assert first.target.model_dump() == {"metric": "filler_count", "operator": "LTE", "value": 5}
    assert first.reason == "직접 정한 기준인데 이번 Take 7"
    assert (second.area, second.slide_number) == ("GAZE", 3)
    assert second.target.model_dump() == {
        "metric": "script_ratio",
        "operator": "LTE",
        "value": 0.52,
    }
    assert [m.priority for m in resp.next_missions] == [1, 2]


def test_failed_criteria_sorted_by_dimension_burden(req):
    req["user_criteria"]["targets"] = [
        target("추임새 5번 이하", "filler_count", "LTE", 5),  # fluency 부담 6
        target("대본 20% 이하", "script_ratio", "LTE", 0.2),  # script_independence 부담 15.7
    ]
    resp, _ = run(req)
    assert descriptions(resp.next_missions)[:2] == ["대본 20% 이하", "추임새 5번 이하"]


def test_failed_criteria_tie_keeps_input_order(req):
    a = target("목소리 크게", "voice_diff_db", "GTE", 0)
    b = target("청중 80% 보기", "audience_ratio", "GTE", 0.8)
    req["user_criteria"]["targets"] = [a, b]
    resp, _ = run(req)
    assert descriptions(resp.next_missions)[:2] == ["목소리 크게", "청중 80% 보기"]
    req["user_criteria"]["targets"] = [b, a]
    resp, _ = run(req)
    assert descriptions(resp.next_missions)[:2] == ["청중 80% 보기", "목소리 크게"]


def busy_request(req):
    """PRIORITY 3개: script_independence 15.7 · content_delivery 15.0 · fluency 14.4"""
    req["take_result"]["problem_segments"].append(
        segment("FILLER_FREQUENT", "FILLER", 3, 70_000, 84_000, 0.6)
    )
    coverage = sample_coverage()
    coverage["evaluations"][0]["scores"]["content_coverage"] = 0.6
    coverage["evaluations"][0]["key_points"] += [
        {"key_point_id": "K2", "importance": "critical", "status": "missing"},
        {"key_point_id": "K3", "importance": "high", "status": "missing"},
    ]
    req["script_coverage"] = coverage
    return req


def test_candidates_and_final_count(req):
    resp, trace = run(busy_request(req))
    assert descriptions(trace.next_mission_candidates) == [
        "추임새 5번 이하",
        "3번 장 대본 응시 비율 52% 이하",
        "3번 장 핵심 내용 전달률 70% 이상",  # 내용은 늘 장 미션
        "분당 군더더기 1.9 이하",  # 3번 장에 58% 몰렸지만 장 값이 없어 Take 미션
    ]
    assert descriptions(resp.next_missions) == descriptions(trace.next_mission_candidates)[:3]
    assert [m.priority for m in resp.next_missions] == [1, 2, 3]


def test_pacing_rhythm_makes_no_mission(req):
    req["take_result"]["problem_segments"].append(
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8)
    )
    resp, trace = run(req)
    assert resp.dimensions["pacing_rhythm"] == "PRIORITY"
    metrics = [m["target"]["metric"] for m in trace.next_mission_candidates]
    assert "cpm" not in metrics and "slide_duration_ms" not in metrics


def test_already_at_final_target_makes_no_mission(req):
    req["take_result"]["areas"]["GAZE"]["slides"][0]["metrics"]["script_ratio"] = 0.15
    _, trace = run(req)
    assert all(m["target"]["metric"] != "script_ratio" for m in trace.next_mission_candidates)
