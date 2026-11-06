"""values.py 단위 시험: 값 읽기 · 차원 상태 · 문제 구간 · 판정 범위 (기능 문서 6-3 · 6-4, L4-01 · L4-05 · L4-06)."""

from __future__ import annotations

import math

import pytest

from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.values import (
    InvalidDataError,
    problem_issues,
    range_for,
    read_dimension_state,
    read_metric,
    read_problems,
)

from .snapshots import make_take, set_value


def test_read_take_value_has_path_and_versions():
    snap = make_take(5, audience_ratio=0.31)
    rv = read_metric(snap, "audience_ratio", None, CFG)
    assert rv.state == "OK" and rv.value == 0.31
    assert rv.path == "take_result.areas.GAZE.take.audience_ratio"
    assert (rv.kind, rv.revision, rv.area) == ("TAKE_RESULT", 1, "GAZE")
    assert rv.rule_version == "gaze-1.3+aaaaaaaaaaaa"


def test_read_slide_value_path_matches_doc_example():
    snap = make_take(5, gaze_slides={3: 0.28})
    rv = read_metric(snap, "audience_ratio", 3, CFG)
    assert rv.value == 0.28
    assert rv.path == "take_result.areas.GAZE.slides[slide_number=3].metrics.audience_ratio"
    assert rv.slide_number == 3


def test_read_missing_slide_is_not_found():
    rv = read_metric(make_take(5), "audience_ratio", 9, CFG)
    assert (rv.state, rv.reason) == ("NOT_FOUND", "SLIDE_MISSING")


def test_read_unknown_metric_is_not_found():
    assert read_metric(make_take(5), "charisma", None, CFG).state == "NOT_FOUND"


def test_fm_L4_01_null_and_unmeasured_are_not_zero():
    # null 값 · 영역 미측정은 UNMEASURED 이고 0 이 아니다
    null_snap = make_take(5, audience_ratio=None)
    rv = read_metric(null_snap, "audience_ratio", None, CFG)
    assert (rv.state, rv.value) == ("UNMEASURED", None)

    un = make_take(5, unmeasured={"GAZE": "TRACKING_LOST"})
    rv = read_metric(un, "audience_ratio", None, CFG)
    assert (rv.state, rv.value, rv.reason) == ("UNMEASURED", None, "TRACKING_LOST")
    assert read_metric(un, "audience_ratio", 3, CFG).state == "UNMEASURED"
    # 미측정이어도 판정 기준 버전은 읽는다(목표 판정이 버전 차이를 먼저 보기 때문)
    assert rv.rule_version == "gaze-1.3+aaaaaaaaaaaa"


@pytest.mark.parametrize(
    ("ratio", "state"),
    [(0.49, "UNMEASURED"), (0.4999, "UNMEASURED"), (0.5, "OK"), (0.51, "OK"), (0.0, "UNMEASURED")],
)
def test_fm_L4_01_scored_ratio_boundary(ratio, state):
    snap = make_take(5, scored_ratio=ratio, content_coverage=0.8)
    rv = read_metric(snap, "content_coverage", None, CFG)
    assert rv.state == state
    assert (rv.value == 0.8) if state == "OK" else (rv.value is None)
    assert rv.path == "script_coverage.take_scores.content_coverage"
    assert rv.kind == "SCRIPT_COVERAGE"


def test_fm_L4_01_content_without_coverage_or_ratio():
    assert read_metric(make_take(5), "content_coverage", None, CFG).state == "NOT_LOADED"
    snap = make_take(5, scored_ratio=0.9)
    snap.results.script_coverage["take_scores"].pop("scored_ratio")
    assert read_metric(snap, "content_coverage", None, CFG).state == "UNMEASURED"
    snap.results.script_coverage["take_scores"]["scored_ratio"] = "많이"
    assert read_metric(snap, "content_coverage", None, CFG).state == "INVALID_DATA"


def test_fm_L4_05_omitted_vs_not_loaded_vs_not_found():
    omitted = make_take(5, omitted_paths=["take_result.areas.GAZE.slides"])
    assert read_metric(omitted, "audience_ratio", 3, CFG).state == "OMITTED"
    assert (
        read_metric(omitted, "audience_ratio", None, CFG).state == "OK"
    )  # Take 값은 빠지지 않았다
    whole = make_take(5, omitted_paths=["take_result"])
    assert read_metric(whole, "cpm", None, CFG).state == "OMITTED"
    none = make_take(5, with_take_result=False)
    assert read_metric(none, "cpm", None, CFG).state == "NOT_LOADED"


@pytest.mark.parametrize(
    ("metric", "area", "bad"),
    [
        ("audience_ratio", "GAZE", "0.3"),  # 문자열
        ("audience_ratio", "GAZE", True),  # bool
        ("audience_ratio", "GAZE", math.nan),
        ("audience_ratio", "GAZE", math.inf),
        ("audience_ratio", "GAZE", 1.2),  # 비율 범위 초과
        ("audience_ratio", "GAZE", -0.1),
        ("cpm", "SPEED", -5),  # 음수 개수 · 속도
        ("duration_ms", "TIME", -1),  # 음수 시간
        ("filler_per_min", "FILLER", -0.5),
    ],
)
def test_fm_L4_06_invalid_take_values(metric, area, bad):
    snap = set_value(make_take(5), area, metric, bad)
    rv = read_metric(snap, metric, None, CFG)
    assert (rv.state, rv.value) == ("INVALID_DATA", None)


def test_fm_L4_06_slide_value_and_duplicate_or_bad_slide_numbers():
    snap = set_value(make_take(5), "GAZE", "audience_ratio", 2, slide_number=3)
    assert read_metric(snap, "audience_ratio", 3, CFG).state == "INVALID_DATA"
    dup = make_take(5)
    dup.results.take_result["areas"]["GAZE"]["slides"][1]["slide_number"] = 1  # 1이 두 번
    assert read_metric(dup, "audience_ratio", 1, CFG).state == "INVALID_DATA"
    zero = make_take(5)
    zero.results.take_result["areas"]["GAZE"]["slides"][0]["slide_number"] = 0
    assert read_metric(zero, "audience_ratio", None, CFG).state == "INVALID_DATA"
    assert read_metric(make_take(5), "audience_ratio", 0, CFG).state == "INVALID_DATA"


def test_invalid_rule_version_type():
    snap = make_take(5)
    snap.results.take_result["criteria"]["GAZE"]["criteria_version"] = 12
    assert read_metric(snap, "audience_ratio", None, CFG).state == "INVALID_DATA"


def test_missing_rule_version_is_none_not_error():
    snap = make_take(5, criteria_versions={"GAZE": None})
    rv = read_metric(snap, "audience_ratio", None, CFG)
    assert rv.state == "OK" and rv.rule_version is None


@pytest.mark.parametrize("state", ["STRENGTH", "PRIORITY", "IMPROVED", "STABLE"])
def test_dimension_state_ok(state):
    snap = make_take(5, dims={"audience_gaze": state})
    rv = read_dimension_state(snap, "audience_gaze")
    assert (rv.state, rv.value, rv.kind) == ("OK", state, "REVIEW")
    assert rv.path == "review.dimensions.audience_gaze"


def test_dimension_state_not_evaluable_no_review_unknown():
    snap = make_take(5, dims={"audience_gaze": "NOT_EVALUABLE", "fluency": "WOW"})
    assert read_dimension_state(snap, "audience_gaze").state == "UNMEASURED"
    assert read_dimension_state(snap, "audience_gaze").reason == "NOT_EVALUABLE"
    unk = read_dimension_state(snap, "fluency")
    assert (unk.state, unk.reason) == ("UNMEASURED", "UNKNOWN_VALUE")
    assert read_dimension_state(snap, "vocal_delivery").state == "NOT_FOUND"
    none = read_dimension_state(make_take(5, dims=None), "audience_gaze")
    assert (none.state, none.reason) == ("EMPTY", "NO_REVIEW")
    omitted = make_take(5, omitted_paths=["review"])
    assert read_dimension_state(omitted, "audience_gaze").state == "OMITTED"


def test_problem_issues_counts_only_reliable_segments():
    snap = make_take(5, issues=[("PACE_FAST", 2, True), ("GAZE_AWAY", 2, False)])
    assert problem_issues(snap, "pacing_rhythm", 2, CFG, areas=("SPEED",)) == ("OK", ["PACE_FAST"])
    # 다른 장에는 문제 없음 (측정됨)
    assert problem_issues(snap, "pacing_rhythm", 1, CFG, areas=("SPEED",)) == ("OK", [])


def test_problem_issues_only_unreliable_is_insufficient():
    snap = make_take(5, issues=[("GAZE_AWAY", 2, False)])
    state, issues = problem_issues(snap, "audience_gaze", 2, CFG)
    assert (state, issues) == ("UNMEASURED", [])
    assert read_problems(snap, "audience_gaze", 2, CFG).reason == "UNRELIABLE_SEGMENTS"
    # Take 범위도 같다
    assert problem_issues(snap, "audience_gaze", None, CFG)[0] == "UNMEASURED"


def test_problem_issues_measured_and_none_is_ok_empty():
    snap = make_take(5)
    assert problem_issues(snap, "audience_gaze", None, CFG) == ("OK", [])
    assert problem_issues(snap, "audience_gaze", 3, CFG) == ("OK", [])


def test_fm_L4_01_problem_issues_unmeasured_area_is_not_no_problem():
    snap = make_take(5, unmeasured={"GAZE": "TRACKING_LOST"})
    assert problem_issues(snap, "audience_gaze", None, CFG) == ("UNMEASURED", [])
    # 한 영역이라도 못 쟀으면 '문제 없음'이라 말할 수 없다 (pacing_rhythm = SPEED · PAUSE · TIME)
    partly = make_take(5, unmeasured={"TIME": "NO_PLAN"})
    assert problem_issues(partly, "pacing_rhythm", None, CFG)[0] == "UNMEASURED"
    # 그래도 찾은 문제는 사실이다
    found = make_take(5, unmeasured={"TIME": "NO_PLAN"}, issues=[("PACE_FAST", None, True)])
    assert problem_issues(found, "pacing_rhythm", None, CFG) == ("OK", ["PACE_FAST"])


def test_problem_issues_slide_must_be_in_area_slides():
    snap = make_take(5)
    assert problem_issues(snap, "audience_gaze", 9, CFG) == ("UNMEASURED", [])
    assert read_problems(snap, "audience_gaze", 9, CFG).reason == "SLIDE_UNMEASURED"


def test_problem_issues_not_loaded_and_omitted_and_invalid():
    assert (
        problem_issues(make_take(5, with_take_result=False), "audience_gaze", None, CFG)[0]
        == "NOT_LOADED"
    )
    om = make_take(5, omitted_paths=["take_result.problem_segments"])
    assert problem_issues(om, "audience_gaze", None, CFG)[0] == "OMITTED"
    bad = make_take(5)
    bad.results.take_result["problem_segments"] = [
        {"issue_type": "PACE_FAST", "area": "SPEED", "reliable": "yes"}
    ]
    assert problem_issues(bad, "pacing_rhythm", None, CFG)[0] == "INVALID_DATA"
    assert problem_issues(make_take(5), "no_such_dim", None, CFG)[0] == "NOT_FOUND"


def test_problem_issues_content_delivery_uses_key_points():
    snap = make_take(5, scored_ratio=0.9, key_points=[(1, "covered"), (2, "missing")])
    assert problem_issues(snap, "content_delivery", None, CFG) == ("OK", ["CONTENT"])
    assert problem_issues(snap, "content_delivery", 1, CFG) == ("OK", [])
    assert problem_issues(snap, "content_delivery", 2, CFG) == ("OK", ["CONTENT"])
    assert problem_issues(snap, "content_delivery", 3, CFG)[0] == "UNMEASURED"
    contra = make_take(5, scored_ratio=0.9, key_points=[(1, "contradicted")])
    assert problem_issues(contra, "content_delivery", None, CFG) == ("OK", ["CONTENT"])
    low = make_take(5, scored_ratio=0.3, key_points=[(1, "missing")])
    assert problem_issues(low, "content_delivery", None, CFG)[0] == "UNMEASURED"
    assert problem_issues(make_take(5), "content_delivery", None, CFG)[0] == "NOT_LOADED"


def test_range_for_cpm_uses_speed_thresholds():
    assert range_for(make_take(5), "cpm", None, CFG) == (275.0, 350.0)
    assert range_for(make_take(5, pace_thresholds=(None, 350.0)), "cpm", None, CFG) == (None, 350.0)
    assert range_for(make_take(5, pace_thresholds=(None, None)), "cpm", None, CFG) is None


def test_range_for_duration_plan_min_max_else_target():
    assert range_for(make_take(5), "duration_ms", None, CFG) == (270000.0, 330000.0)
    only_target = make_take(5, plan={"target_ms": 300000, "min_ms": None, "max_ms": None})
    assert range_for(only_target, "duration_ms", None, CFG) == (300000.0, 300000.0)
    one_sided = make_take(5, plan={"target_ms": 300000, "min_ms": 270000, "max_ms": None})
    assert range_for(one_sided, "duration_ms", None, CFG) == (270000.0, None)
    assert range_for(make_take(5, plan=None), "duration_ms", None, CFG) is None


def test_range_for_slide_duration_uses_slide_target():
    snap = make_take(5, slide_targets={3: 90000})
    assert range_for(snap, "slide_duration_ms", 3, CFG) == (90000.0, 90000.0)
    assert range_for(snap, "slide_duration_ms", None, CFG) is None
    assert range_for(snap, "slide_duration_ms", 9, CFG) is None


def test_range_for_non_range_metric_and_missing_result():
    assert range_for(make_take(5), "audience_ratio", None, CFG) is None
    assert range_for(make_take(5, with_take_result=False), "cpm", None, CFG) is None


@pytest.mark.parametrize(
    "snap",
    [
        make_take(5, pace_thresholds=(400.0, 300.0)),  # low > high
    ],
)
def test_range_for_inverted_range_is_invalid(snap):
    with pytest.raises(InvalidDataError):
        range_for(snap, "cpm", None, CFG)


def test_range_for_inverted_plan_and_bad_bounds():
    with pytest.raises(InvalidDataError):
        range_for(make_take(5, plan={"min_ms": 330000, "max_ms": 270000}), "duration_ms", None, CFG)
    with pytest.raises(InvalidDataError):
        range_for(make_take(5, plan={"min_ms": -1, "max_ms": 10}), "duration_ms", None, CFG)
    with pytest.raises(InvalidDataError):
        range_for(make_take(5, plan={"min_ms": "a", "max_ms": 10}), "duration_ms", None, CFG)


# ── 검토 반영 ────────────────────────────────────────────────────────────────


def test_rule_version_is_read_even_when_the_value_is_omitted():
    snap = make_take(5, omitted_paths=["take_result.areas.GAZE.take.audience_ratio"])
    rv = read_metric(snap, "audience_ratio", None, CFG)
    assert (rv.state, rv.rule_version) == ("OMITTED", "gaze-1.3+aaaaaaaaaaaa")
    both = make_take(5, omitted_paths=["take_result.areas.GAZE", "take_result.criteria.GAZE"])
    rv = read_metric(both, "audience_ratio", None, CFG)
    assert (rv.state, rv.rule_version) == ("OMITTED", None)


def test_scored_ratio_path_omitted_blocks_content_values():
    snap = make_take(
        5, scored_ratio=0.9, omitted_paths=["script_coverage.take_scores.scored_ratio"]
    )
    assert read_metric(snap, "content_coverage", None, CFG).state == "OMITTED"
    whole = make_take(5, scored_ratio=0.9, omitted_paths=["script_coverage.take_scores"])
    assert read_metric(whole, "content_coverage", None, CFG).state == "OMITTED"


def test_missing_problem_segments_is_insufficient_not_empty():
    absent = make_take(5)
    del absent.results.take_result["problem_segments"]
    pr = read_problems(absent, "audience_gaze", None, CFG)
    assert (pr.state, pr.reason, pr.issues) == ("UNMEASURED", "SEGMENTS_MISSING", [])
    assert read_problems(make_take(5), "audience_gaze", None, CFG).state == "OK"  # 명시적 빈 목록


def test_range_sources_report_the_bound_paths():
    from pitch_coach_ai.features.chat_agent.values import OmittedDataError, range_sources

    rng, src = range_sources(make_take(5), "cpm", None, CFG)
    assert rng == (275.0, 350.0)
    assert [(s.role, s.path, s.value) for s in src] == [
        ("low", "take_result.criteria.SPEED.issues.PACE_SLOW.threshold", 275.0),
        ("high", "take_result.criteria.SPEED.issues.PACE_FAST.threshold", 350.0),
    ]
    rng, src = range_sources(make_take(5, plan={"target_ms": 300000}), "duration_ms", None, CFG)
    assert rng == (300000.0, 300000.0) and [s.role for s in src] == ["both"]
    assert range_sources(make_take(5), "audience_ratio", None, CFG) == (None, [])
    with pytest.raises(OmittedDataError):
        range_for(make_take(5, omitted_paths=["take_result.plan"]), "duration_ms", None, CFG)
    bad = make_take(5)
    bad.results.take_result["plan"] = "x"
    with pytest.raises(InvalidDataError):
        range_for(bad, "duration_ms", None, CFG)


def test_is_omitted_ancestor_equal_descendant_with_path_boundaries():
    from pitch_coach_ai.features.chat_agent.values import is_omitted

    def om(*paths):
        return make_take(5, omitted_paths=list(paths))

    p = "take_result.criteria.SPEED.issues.PACE_FAST.threshold"
    slide = "take_result.areas.GAZE.slides[slide_number=3]"
    assert is_omitted(om(p), p)  # 같음
    assert is_omitted(om("take_result.criteria.SPEED"), p)  # 조상
    assert is_omitted(om(p + ".x"), p)  # 자손
    assert is_omitted(om(slide), slide + ".metrics.x")
    assert is_omitted(om(slide + ".metrics.x"), slide)
    assert not is_omitted(om("take_result.criteria.SPEED.issues.PACE"), p)  # 이름이 앞부분만 같다
    assert not is_omitted(om(p), "take_result.criteria.SPEED.issues.PACE")
    assert not is_omitted(om(slide), "take_result.areas.GAZE.slides[slide_number=30]")
    assert not is_omitted(om(p), "take_result.criteria.SPEED", include_descendants=False)


def test_content_problems_with_omitted_scored_ratio_is_omitted():
    omitted = ["script_coverage.take_scores.scored_ratio"]
    snap = make_take(5, scored_ratio=0.9, key_points=[(1, "missing")], omitted_paths=omitted)
    assert problem_issues(snap, "content_delivery", None, CFG)[0] == "OMITTED"
    assert read_metric(snap, "content_coverage", None, CFG).state == "OMITTED"
