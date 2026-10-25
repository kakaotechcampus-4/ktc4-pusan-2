"""7단계 다음 미션."""

from __future__ import annotations

from review import load_config
from review.schemas import MAX_CRITERIA_TARGETS, MAX_DESCRIPTION_CHARS, MAX_NEXT_MISSIONS

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
    req["take_result"]["areas"]["FILLER"]["slides"] = [
        {"slide_number": 3, "measured_ratio": 1.0, "metrics": {"filler_per_min": 3.0}}
    ]
    coverage = sample_coverage()
    coverage["evaluations"][0]["scores"]["content_coverage"] = 0.6
    coverage["evaluations"][0]["key_points"] += [
        {"key_point_id": "K2", "importance": "critical", "status": "missing"},
        {"key_point_id": "K3", "importance": "high", "status": "missing"},
    ]
    req["script_coverage"] = coverage
    return req


def test_candidates_and_final_are_same_without_llm(req):
    # LLM 없이는 후보 전부가 다음 미션 (사용자 기준 FAILED 1 + PRIORITY 3)
    resp, trace = run(busy_request(req))
    assert descriptions(trace.next_mission_candidates) == [
        "추임새 5번 이하",
        "3번 장 대본 응시 비율 52% 이하",
        "3번 장 핵심 내용 전달률 70% 이상",  # 내용은 늘 장 미션
        "3번 장 분당 군더더기 2.5 이하",  # 3번 장에 58% 몰림
    ]
    assert descriptions(resp.next_missions) == descriptions(trace.next_mission_candidates)
    assert [m.priority for m in resp.next_missions] == [1, 2, 3, 4]


def test_missing_slide_value_skips_candidate_but_keeps_recurring(req):
    # 부담이 몰린 장의 값이 없으면(그 장 측정 비율 미달) 후보를 만들지 않고,
    # 반복 문제는 그 장으로 낸다
    req = busy_request(req)
    req["take_result"]["areas"]["FILLER"]["slides"][0]["metrics"] = None
    resp, _ = run(req)
    assert "분당 군더더기" not in " ".join(descriptions(resp.next_missions))
    fluency = [r for r in resp.recurring_issues if r.dimension == "fluency"]
    assert [(r.area, r.slide_number) for r in fluency] == [("FILLER", 3)]


def test_all_failed_criteria_kept_and_priority_capped(req):
    # 실패한 사용자 목표는 개수와 상관없이 모두, PRIORITY 후보는 priority_candidate_max 개까지
    req = busy_request(req)
    req["user_criteria"]["targets"] = [
        target(f"추임새 {n}번 이하", "filler_count", "LTE", n) for n in range(5)
    ]
    resp, _ = run(req, load_config(next_mission={"priority_candidate_max": 1}))
    assert descriptions(resp.next_missions) == [
        *(f"추임새 {n}번 이하" for n in range(5)),
        "3번 장 대본 응시 비율 52% 이하",
    ]


def test_next_missions_capped_at_contract_limit(req):
    req = busy_request(req)
    req["user_criteria"]["targets"] = [
        target("가" * 600, "filler_count", "LTE", 0) for _ in range(MAX_CRITERIA_TARGETS)
    ]
    resp, _ = run(req)
    assert len(resp.next_missions) == MAX_NEXT_MISSIONS  # PRIORITY 후보가 들어갈 자리가 없다
    assert all(len(m.description) == MAX_DESCRIPTION_CHARS for m in resp.next_missions)


def pacing(resp):
    return [m for m in resp.next_missions if m.area in ("SPEED", "PAUSE", "TIME")]


def test_slide_over_makes_slide_mission_toward_slide_target(req):
    req["take_result"]["areas"]["TIME"]["slides"][0]["metrics"]["slide_duration_ms"] = 90_000
    req["take_result"]["problem_segments"].append(
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8)  # 12.0
    )
    resp, _ = run(req)
    assert resp.dimensions["pacing_rhythm"] == "PRIORITY"
    [m] = pacing(resp)
    assert (m.area, m.slide_number, m.description) == ("TIME", 3, "3번 장 머문 시간 1분 20초 이하")
    # 90초에서 한 걸음(10초), 장 목표 60초를 넘지 않음
    assert m.target.model_dump() == {
        "metric": "slide_duration_ms",
        "operator": "LTE",
        "value": 80_000,
    }


def test_already_at_slide_target_makes_no_mission(req):
    # 3번 장 머문 시간 60초 = 장 목표 60초
    req["take_result"]["problem_segments"].append(
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8)
    )
    resp, _ = run(req)
    assert resp.dimensions["pacing_rhythm"] == "PRIORITY"
    assert pacing(resp) == []
    pacing_issue = [r for r in resp.recurring_issues if r.dimension == "pacing_rhythm"]
    assert [(r.area, r.slide_number) for r in pacing_issue] == [("TIME", 3)]  # 반복 문제는 낸다


def test_time_over_makes_take_mission_toward_plan_max(req):
    req["take_result"]["plan"] = {"target_ms": 150_000, "min_ms": 130_000, "max_ms": 170_000}
    req["take_result"]["problem_segments"].append(
        segment("TIME_OVER", "TIME", 9, 170_000, 182_000, 1.0)  # 12.0, 시간을 넘긴 순간 9번 장
    )
    resp, _ = run(req)
    [m] = pacing(resp)
    # 182초에서 한 걸음(30초) 줄이면 152초지만 plan.max_ms 170초를 넘지 않음. 장은 싣지 않는다
    assert (m.slide_number, m.description) == (None, "발표 시간 2분 50초 이하")
    assert m.target.model_dump() == {"metric": "duration_ms", "operator": "LTE", "value": 170_000}
    pacing_issue = [r for r in resp.recurring_issues if r.dimension == "pacing_rhythm"]
    assert [(r.area, r.slide_number) for r in pacing_issue] == [("TIME", None)]


def test_slide_is_chosen_within_issue_not_area(req):
    # 고른 문제는 SLIDE_OVER(12.0 > TIME_OVER 11.0). 영역 전체로 보면 9번 장이 가장 크지만
    # TIME_OVER 부담은 섞지 않아 SLIDE_OVER 의 장(3 · 4번 동률 → 작은 3번)을 고른다
    req["take_result"]["areas"]["TIME"]["slides"][0]["metrics"]["slide_duration_ms"] = 90_000
    req["take_result"]["problem_segments"] += [
        segment("SLIDE_OVER", "TIME", 3, 110_000, 117_500, 0.8),  # 6.0
        segment("SLIDE_OVER", "TIME", 4, 130_000, 137_500, 0.8),  # 6.0
        segment("TIME_OVER", "TIME", 9, 171_000, 182_000, 1.0),  # 11.0
    ]
    resp, _ = run(req)
    assert [(m.slide_number, m.target.metric) for m in pacing(resp)] == [(3, "slide_duration_ms")]


def _speed_criteria(req, slow, fast):
    req["take_result"]["criteria"]["SPEED"] = {
        "criteria_version": "pace-1.0+000000000000",
        "issues": {
            "PACE_SLOW": {"metric": "cpm", "direction": "LOWER_IS_WORSE", "threshold": slow},
            "PACE_FAST": {"metric": "cpm", "direction": "HIGHER_IS_WORSE", "threshold": fast},
        },
    }


def test_pace_direction_and_final_follow_issue_and_criteria(req):
    _speed_criteria(req, 250, 300)
    req["take_result"]["problem_segments"].append(
        segment("PACE_FAST", "SPEED", None, 0, 30_000, 0.5)  # 15.0
    )
    [m] = pacing(run(req)[0])
    # 빠름: 318.5 에서 한 걸음(20) 줄이면 298.5 지만 PACE_FAST 기준 300 을 넘지 않음
    assert m.target.model_dump() == {"metric": "cpm", "operator": "LTE", "value": 300}

    req["take_result"]["areas"]["SPEED"]["take"]["cpm"] = 220
    req["take_result"]["problem_segments"][-1]["issue_type"] = "PACE_SLOW"
    [m] = pacing(run(req)[0])
    # 느림: 220 에서 한 걸음(20) 올린 240, PACE_SLOW 기준 250 을 넘지 않음
    assert m.target.model_dump() == {"metric": "cpm", "operator": "GTE", "value": 240}


def test_pace_without_criteria_makes_no_mission(req):
    req["take_result"]["problem_segments"].append(
        segment("PACE_FAST", "SPEED", None, 0, 30_000, 0.5)
    )
    resp, _ = run(req)
    assert resp.dimensions["pacing_rhythm"] == "PRIORITY"
    assert pacing(resp) == []


def test_long_silence_mission(req):
    req["take_result"]["problem_segments"].append(
        segment("LONG_SILENCE", "PAUSE", None, 0, 20_000, 0.6)  # 12.0
    )
    [m] = pacing(run(req)[0])
    assert (m.area, m.description) == ("PAUSE", "긴 침묵 0번 이하")
    assert m.target.model_dump() == {"metric": "long_silence_count", "operator": "LTE", "value": 0}


def test_already_at_final_target_makes_no_mission(req):
    req["take_result"]["areas"]["GAZE"]["slides"][0]["metrics"]["script_ratio"] = 0.15
    _, trace = run(req)
    assert all(m["target"]["metric"] != "script_ratio" for m in trace.next_mission_candidates)
