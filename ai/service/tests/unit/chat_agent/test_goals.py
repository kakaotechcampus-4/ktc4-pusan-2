"""goals.py 단위 시험: 두 Take 비교 · 목표 초안 · 목표 판정 (기능 문서 6-4, L4-01 · 06 · 08~12 · 25, L6-23)."""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.goals import (
    build_goal_draft,
    compare_takes,
    judge_goal,
    range_distance,
)
from pitch_coach_ai.features.chat_agent.schemas import Goal, GoalDraft

from .snapshots import make_take, make_version_of, new_registry, set_value

VERSION_OF = make_version_of("p-1")


def cmp(prev, cur, metric, slide=None, *, reg=None, cfg=CFG):
    return compare_takes(
        prev,
        cur,
        metric,
        slide,
        pitch_id="p-1",
        registry=reg or new_registry(),
        version_of=VERSION_OF,
        cfg=cfg,
    )


# ── 방향표: HIGHER · LOWER · 같음 ──────────────────────────────────────────


def test_higher_improved_and_worsened_and_same():
    t4, t5 = make_take(4, audience_ratio=0.28), make_take(5, audience_ratio=0.33)
    c = cmp(t4, t5, "audience_ratio")
    assert (c.comparable, c.outcome, c.previous, c.current) == (True, "IMPROVED", 0.28, 0.33)
    assert c.previous_range is None and c.range_changed is None and c.previous_distance is None
    assert cmp(t5, t4, "audience_ratio").outcome == "WORSENED"
    assert cmp(t4, make_take(5, audience_ratio=0.28), "audience_ratio").outcome == "SAME"


def test_lower_direction_filler():
    a, b = make_take(4, filler_per_min=2.0), make_take(5, filler_per_min=1.2)
    assert cmp(a, b, "filler_per_min").outcome == "IMPROVED"
    assert cmp(b, a, "filler_per_min").outcome == "WORSENED"


def test_slide_value_comparison():
    a = make_take(4, gaze_slides={3: 0.2})
    b = make_take(5, gaze_slides={3: 0.4})
    c = cmp(a, b, "audience_ratio", 3)
    assert c.outcome == "IMPROVED" and c.slide_number == 3


def test_fm_L4_10_no_direction_metric_is_not_compared():
    c = cmp(make_take(4), make_take(5), "pause_count")
    assert (c.comparable, c.reason, c.outcome) == (False, "NO_DIRECTION", None)
    assert cmp(make_take(4), make_take(5), "no_such_metric").reason == "NO_DIRECTION"


def test_fm_L4_10_equal_raw_values_are_same_not_improved_or_worsened():
    # 표시 반올림이 아니라 원래 값으로 가른다: 0.3099 vs 0.3101 은 화면에서 31% 로 같아 보여도 SAME 이 아니다
    a, b = make_take(4, audience_ratio=0.3099), make_take(5, audience_ratio=0.3101)
    assert cmp(a, b, "audience_ratio").outcome == "IMPROVED"
    assert cmp(a, make_take(5, audience_ratio=0.3099), "audience_ratio").outcome == "SAME"


# ── 범위 지표 ──────────────────────────────────────────────────────────────


def test_range_distance_function():
    assert range_distance(300, 275, 350) == 0
    assert range_distance(400, 275, 350) == 50
    assert range_distance(250, 275, 350) == 25
    assert range_distance(100, None, 350) == 0  # 아래가 열려 있음
    assert range_distance(400, None, 350) == 50
    assert range_distance(300, 275, None) == 0


def test_range_both_in_range_improved_worsened_same_distance():
    both = cmp(make_take(4, cpm=290), make_take(5, cpm=320), "cpm")
    assert both.outcome == "BOTH_IN_RANGE"
    assert (both.previous_distance, both.current_distance) == (0, 0)
    assert (both.previous_range.low, both.previous_range.high) == (275, 350)
    better = cmp(make_take(4, cpm=420), make_take(5, cpm=380), "cpm")
    assert better.outcome == "IMPROVED" and (better.previous_distance, better.current_distance) == (
        70,
        30,
    )
    worse = cmp(make_take(4, cpm=290), make_take(5, cpm=380), "cpm")
    assert worse.outcome == "WORSENED"
    same_dist = cmp(make_take(4, cpm=250), make_take(5, cpm=375), "cpm")  # 25 / 25
    assert same_dist.outcome == "SAME_DISTANCE"
    assert cmp(make_take(4, cpm=420), make_take(5, cpm=420), "cpm").outcome == "SAME"


def test_fm_L4_09_one_sided_range_and_json_has_no_infinity():
    a = make_take(4, pace_thresholds=(None, 350.0), cpm=400)
    b = make_take(5, pace_thresholds=(None, 350.0), cpm=360)
    c = cmp(a, b, "cpm")
    assert c.outcome == "IMPROVED"
    assert (c.previous_range.low, c.previous_range.high) == (None, 350)
    dumped = c.model_dump_json()
    assert "Infinity" not in dumped and "inf" not in dumped.lower().replace("information", "")
    assert json.loads(dumped)["previous_range"]["low"] is None
    # 값이 아래로 가도 아래가 열려 있으니 거리는 0
    low_side = cmp(
        make_take(4, pace_thresholds=(None, 350.0), cpm=100),
        make_take(5, pace_thresholds=(None, 350.0), cpm=50),
        "cpm",
    )
    assert low_side.outcome == "BOTH_IN_RANGE"


def test_fm_L4_09_range_changed_uses_each_range_distance():
    # 같은 발표 시간(원래 값 다름)이지만 계획 범위가 다르다: 각 범위 대비 거리로 가른다
    a = make_take(
        4, duration_ms=360000.0, plan={"target_ms": 300000, "min_ms": 270000, "max_ms": 330000}
    )
    b = make_take(
        5, duration_ms=380000.0, plan={"target_ms": 360000, "min_ms": 330000, "max_ms": 390000}
    )
    c = cmp(a, b, "duration_ms")
    assert c.range_changed is True
    assert (c.previous_distance, c.current_distance) == (30000, 0)
    assert (
        c.outcome == "IMPROVED"
    )  # 목표를 바꾼 덕이지 실력 향상이 아니다: 문장은 각 목표 대비 거리로 쓴다
    assert (c.previous_range.low, c.previous_range.high) == (270000, 330000)
    assert (c.current_range.low, c.current_range.high) == (330000, 390000)


def test_fm_L4_09_same_value_with_different_ranges_is_same_but_reports_distances():
    a = make_take(
        4, duration_ms=340000.0, plan={"target_ms": 300000, "min_ms": 270000, "max_ms": 330000}
    )
    b = make_take(
        5, duration_ms=340000.0, plan={"target_ms": 360000, "min_ms": 330000, "max_ms": 390000}
    )
    c = cmp(a, b, "duration_ms")
    assert c.outcome == "SAME" and c.range_changed is True
    assert (c.previous_distance, c.current_distance) == (10000, 0)


def test_fm_L4_09_same_range_not_changed():
    c = cmp(make_take(4, duration_ms=250000.0), make_take(5, duration_ms=260000.0), "duration_ms")
    assert c.range_changed is False and c.outcome == "IMPROVED"


def test_fm_L4_09_inverted_range_blocks_comparison():
    a = make_take(4, pace_thresholds=(400.0, 300.0))
    c = cmp(a, make_take(5), "cpm")
    assert (c.comparable, c.reason) == (False, "INVALID_DATA")
    assert cmp(make_take(4, plan=None), make_take(5), "duration_ms").reason == "NO_RANGE"


def test_slide_duration_target_range():
    a = make_take(4, slide_targets={3: 60000}, slide_durations={3: 80000})
    b = make_take(5, slide_targets={3: 90000}, slide_durations={3: 80000})
    c = cmp(a, b, "slide_duration_ms", 3)
    assert c.range_changed is True and c.outcome == "SAME"
    assert (c.previous_distance, c.current_distance) == (20000, 10000)


# ── 비교 자격 (L4-08) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("prev_kw", "cur_kw", "reason"),
    [
        ({}, {"completed_fully": False}, "NOT_COMPLETED_FULLY"),
        ({"completed_fully": None}, {}, "NOT_COMPLETED_FULLY"),
        ({"script_version_id": "sv-1"}, {"script_version_id": "sv-2"}, "DIFFERENT_SCRIPT_VERSION"),
        ({}, {"script_version_id": None}, "MISSING_VERSION"),
        ({"standard_version": 1}, {"standard_version": 2}, "DIFFERENT_CRITERIA_VERSION"),
        ({"standard_version": None}, {"standard_version": 1}, "DIFFERENT_CRITERIA_VERSION"),
        ({}, {"criteria_versions": {"GAZE": "gaze-1.4+zzzzzzzzzzzz"}}, "RULE_VERSION_CHANGED"),
        ({"criteria_versions": {"GAZE": None}}, {}, "MISSING_VERSION"),
        ({"audience_ratio": None}, {}, "NO_VALUE"),
        ({}, {"unmeasured": {"GAZE": "TRACKING_LOST"}}, "NO_VALUE"),
        ({}, {"with_take_result": False}, "NOT_LOADED"),
        ({}, {"omitted_paths": ["take_result.areas.GAZE"]}, "OMITTED"),
    ],
)
def test_fm_L4_08_each_condition_blocks_comparison(prev_kw, cur_kw, reason):
    c = cmp(make_take(4, **prev_kw), make_take(5, **cur_kw), "audience_ratio")
    assert c.comparable is False and c.reason == reason
    assert c.previous is None and c.current is None and c.outcome is None
    assert c.evidence_ids == []


def test_fm_L4_08_same_standard_version_null_on_both_is_comparable():
    assert cmp(
        make_take(4, standard_version=None), make_take(5, standard_version=None), "audience_ratio"
    ).comparable


def test_fm_L4_01_null_never_becomes_zero_improvement():
    # 앞 Take 값이 null 이면 0 으로 보정해 '좋아짐'이 되지 않는다
    c = cmp(make_take(4, audience_ratio=None), make_take(5, audience_ratio=0.3), "audience_ratio")
    assert (c.comparable, c.reason, c.outcome) == (False, "NO_VALUE", None)


def test_fm_L4_01_content_metric_scored_ratio_boundary():
    a = make_take(4, scored_ratio=0.5, content_coverage=0.6)
    ok = make_take(5, scored_ratio=0.5, content_coverage=0.8)
    low = make_take(5, scored_ratio=0.49, content_coverage=0.8)
    c = cmp(a, ok, "content_coverage")
    assert (c.comparable, c.outcome) == (
        True,
        "IMPROVED",
    )  # 내용 전달 지표에는 코치 판정 기준 버전이 없다
    assert cmp(a, low, "content_coverage").reason == "NO_VALUE"


@pytest.mark.parametrize("bad", ["x", True, math.nan, math.inf, 1.5, -0.1])
def test_fm_L4_06_invalid_values_stop_comparison(bad):
    cur = set_value(make_take(5), "GAZE", "audience_ratio", bad)
    c = cmp(make_take(4), cur, "audience_ratio")
    assert (c.comparable, c.reason) == (False, "INVALID_DATA")


def test_fm_L4_06_duplicate_slide_numbers_stop_slide_comparison():
    cur = make_take(5)
    cur.results.take_result["areas"]["GAZE"]["slides"][1]["slide_number"] = 1
    assert cmp(make_take(4), cur, "audience_ratio", 1).reason == "INVALID_DATA"


# ── 근거 (L4-25) ──────────────────────────────────────────────────────────


def test_fm_L4_25_evidence_records_and_recomputable_computed_values():
    reg = new_registry()
    c = cmp(
        make_take(4, audience_ratio=0.28),
        make_take(5, audience_ratio=0.33),
        "audience_ratio",
        reg=reg,
    )
    evs = [reg.get(i) for i in c.evidence_ids]
    records = [e for e in evs if e.origin == "RECORD"]
    computed = {e.calc: e for e in evs if e.origin == "COMPUTED"}
    assert [e.take_number for e in records] == [4, 5]
    assert records[1].path == "take_result.areas.GAZE.take.audience_ratio"
    assert set(computed) == {"DIFF", "OUTCOME"}
    diff = computed["DIFF"]
    assert diff.inputs == [r.evidence_id for r in records]
    # 검사기는 입력 근거로 다시 계산한다: 33% - 28% = 5%p 가 아니라 잘못된 값이면 걸린다
    assert math.isclose(diff.value, records[1].value - records[0].value)
    assert computed["OUTCOME"].value == "IMPROVED"
    assert all(reg.is_current(i) for i in c.evidence_ids)
    # 지문은 두 Take 의 버전을 모두 담는다
    basis = reg.basis_for(c.evidence_ids)
    assert {t.take_id for r in basis.records for t in r.takes} == {"take-4", "take-5"}


def test_fm_L4_25_range_comparison_has_distance_evidence():
    reg = new_registry()
    c = cmp(make_take(4, cpm=420), make_take(5, cpm=380), "cpm", reg=reg)
    evs = [reg.get(i) for i in c.evidence_ids]
    dist = [e for e in evs if e.calc == "DISTANCE"]
    assert sorted(e.value for e in dist) == [30, 70]
    out = next(e for e in evs if e.calc == "OUTCOME")
    assert set(out.inputs) >= {e.evidence_id for e in dist}


def test_fm_L4_25_not_comparable_issues_no_evidence():
    reg = new_registry()
    cmp(make_take(4), make_take(5, completed_fully=False), "audience_ratio", reg=reg)
    assert reg.items == {}


# ── 목표 초안 (L4-11 · L4-12 · L6-23) ───────────────────────────────────────


def draft(snap, metric, slide=None, cfg=CFG):
    return build_goal_draft(snap, metric, slide, pitch_id="p-1", cfg=cfg)


def test_fm_L4_11_draft_gt_base_fixes_min_delta_and_versions():
    d = draft(make_take(4, gaze_slides={3: 0.28}), "audience_ratio", 3)
    assert isinstance(d, GoalDraft)
    assert (d.operator, d.min_delta, d.slide_number) == ("gt_base", 0.05, 3)
    assert d.base.value == 0.28 and d.base.take_number == 4 and d.base.kind == "TAKE_RESULT"
    assert d.base.metric_rule_version == "gaze-1.3+aaaaaaaaaaaa" and d.base.range is None
    assert (d.script_version_id, d.standard_version) == ("sv-1", 1)
    assert d.goal_rule_version == CFG.goal_rule_version()
    Goal(
        **d.model_dump(), version=1, created_at="2026-11-04T00:00:00Z", status="OPEN"
    )  # 저장 가능한 모양


def test_fm_L4_11_draft_lt_base_filler():
    d = draft(make_take(4, filler_per_min=2.4), "filler_per_min")
    assert (d.operator, d.min_delta, d.base.value) == ("lt_base", 0.5, 2.4)


def test_fm_L4_11_draft_in_range_has_fixed_range_and_no_min_delta():
    d = draft(make_take(4), "cpm")
    assert (d.operator, d.min_delta) == ("in_range", None)
    assert (d.base.range.low, d.base.range.high) == (275, 350)
    d2 = draft(make_take(4), "duration_ms")
    assert (d2.base.range.low, d2.base.range.high) == (270000, 330000)


@pytest.mark.parametrize(
    "metric", ["pause_count", "script_ratio", "charisma", "slide_duration_ms", "content_coverage"]
)
def test_fm_L4_11_draft_refuses_metric_outside_the_four(metric):
    assert draft(make_take(4), metric) == "UNMEASURABLE_METRIC"


def test_fm_L6_23_unmeasurable_goal_has_no_draft():
    # "자연스럽게 말하기" 같은 잴 수 없는 목표는 지표가 없다 → 저장 선택지를 만들 수 없다
    assert draft(make_take(4), "naturalness") == "UNMEASURABLE_METRIC"


def test_fm_L4_11_draft_error_codes():
    assert draft(make_take(4, audience_ratio=None), "audience_ratio") == "NO_VALUE"
    assert draft(make_take(4, unmeasured={"GAZE": "X"}), "audience_ratio") == "NO_VALUE"
    assert draft(make_take(4), "audience_ratio", 9) == "NO_VALUE"  # 없는 장
    assert draft(make_take(4, plan=None), "duration_ms") == "NO_RANGE"
    assert draft(make_take(4, pace_thresholds=(500.0, 300.0)), "cpm") == "INVALID_DATA"
    assert (
        draft(set_value(make_take(4), "GAZE", "audience_ratio", 3), "audience_ratio")
        == "INVALID_DATA"
    )
    assert draft(make_take(4, criteria_versions={"GAZE": None}), "audience_ratio") == "NO_VERSION"
    assert draft(make_take(4, with_take_result=False), "audience_ratio") == "NO_VALUE"


@pytest.mark.parametrize("bad_delta", [0, -0.1])
def test_fm_L4_12_config_refuses_zero_or_negative_min_delta(bad_delta):
    # 설정 단계에서 막힌다(최소 개선폭은 유한 양수 필수)
    with pytest.raises(ValidationError):
        load_config(
            goal_metrics={
                "audience_ratio": {"area": "GAZE", "operators": ["gt_base"], "min_delta": bad_delta}
            }
        )


# ── 목표 판정 (L4-12) ──────────────────────────────────────────────────────


def goal_from(base_snap, metric="audience_ratio", slide=None, **overrides) -> Goal:
    d = draft(base_snap, metric, slide)
    assert isinstance(d, GoalDraft), d
    data = d.model_dump()
    data.update(overrides)
    return Goal(**data, version=1, created_at="2026-11-04T00:00:00Z", status="OPEN")


def judge(goal, take, cfg=CFG, reg=None):
    return judge_goal(goal, take, registry=reg or new_registry(), version_of=VERSION_OF, cfg=cfg)


@pytest.mark.parametrize(
    ("current", "status"),
    [(0.33, "ACHIEVED"), (0.335, "ACHIEVED"), (0.3299, "NOT_ACHIEVED"), (0.20, "NOT_ACHIEVED")],
)
def test_fm_L4_12_gt_base_min_delta_boundary(current, status):
    goal = goal_from(make_take(4, audience_ratio=0.28))
    j = judge(goal, make_take(5, audience_ratio=current))
    assert (j.status, j.terminal, j.observed) == (status, True, current)


def test_fm_L4_12_min_delta_boundary_equal_is_achieved_even_with_float_noise():
    # 0.30 - 0.25 = 0.04999999999999999 (부동소수 오차) 이지만 정확히 최소 개선폭이다
    goal = goal_from(make_take(4, audience_ratio=0.25))
    assert judge(goal, make_take(5, audience_ratio=0.30)).status == "ACHIEVED"
    assert judge(goal, make_take(5, audience_ratio=0.2999)).status == "NOT_ACHIEVED"


def test_fm_L4_12_lt_base_boundary():
    goal = goal_from(make_take(4, filler_per_min=2.0), "filler_per_min")
    assert judge(goal, make_take(5, filler_per_min=1.5)).status == "ACHIEVED"  # 정확히 0.5 줄임
    assert judge(goal, make_take(5, filler_per_min=1.6)).status == "NOT_ACHIEVED"
    assert judge(goal, make_take(5, filler_per_min=3.0)).status == "NOT_ACHIEVED"


def test_fm_L4_12_in_range_uses_frozen_base_range_not_current_take_range():
    goal = goal_from(make_take(4, cpm=400), "cpm")
    # 지금 Take 의 기준이 느슨해져도(범위 200~500) 고정한 275~350 으로 판정한다
    cur_in = make_take(5, cpm=300, pace_thresholds=(200.0, 500.0))
    assert judge(goal, cur_in).status == "ACHIEVED"
    cur_out = make_take(5, cpm=400, pace_thresholds=(200.0, 500.0))
    assert judge(goal, cur_out).status == "NOT_ACHIEVED"
    assert judge(goal, make_take(5, cpm=350)).status == "ACHIEVED"  # 경계 포함
    assert judge(goal, make_take(5, cpm=351)).status == "NOT_ACHIEVED"


def test_fm_L4_12_min_delta_frozen_in_goal():
    goal = goal_from(make_take(4, audience_ratio=0.28))
    assert goal.min_delta == 0.05
    take = make_take(5, audience_ratio=0.34)
    assert judge(goal, take).status == "ACHIEVED"
    # 목표에 고정한 값을 쓴다(지금 설정의 값이 아니다)
    strict = goal.model_copy(update={"min_delta": 0.2})
    assert judge(strict, take).status == "NOT_ACHIEVED"


def test_fm_L4_12_same_goal_same_take_gives_same_result_after_config_change():
    # 설정(최소 개선폭)이 바뀌어도 저장된 목표는 자기 고정 값으로, 계산식 판이 지원되면 같은 결과다
    goal = goal_from(make_take(4, audience_ratio=0.28))
    take = make_take(5, audience_ratio=0.34)
    cfg2 = load_config(
        goal_metrics={
            "audience_ratio": {"area": "GAZE", "operators": ["gt_base"], "min_delta": 0.2}
        }
    )
    assert cfg2.goal_rule_version() != CFG.goal_rule_version()
    assert goal.goal_rule_version.split("+")[0] == cfg2.goal_rule_version().split("+")[0]
    before, after = judge(goal, take), judge(goal, take, cfg=cfg2)
    assert (
        (before.status, before.terminal, before.observed, before.reason_code)
        == (
            after.status,
            after.terminal,
            after.observed,
            after.reason_code,
        )
        == ("ACHIEVED", True, 0.34, None)
    )
    # 구간 목표도 마찬가지
    rng_goal = goal_from(make_take(4, cpm=400), "cpm")
    assert (
        judge(rng_goal, make_take(5, cpm=300)).status
        == judge(rng_goal, make_take(5, cpm=300), cfg=cfg2).status
    )


def test_goal_with_unsupported_algorithm_is_unknown_and_asks_for_a_new_goal():
    goal = goal_from(make_take(4), goal_rule_version="goal-algo-99+abcdef012345")
    j = judge(goal, make_take(5, audience_ratio=0.9))
    assert (j.status, j.terminal, j.reason_code, j.evidence_ids) == (
        "UNKNOWN",
        False,
        "GOAL_RULE_CHANGED",
        [],
    )


def test_fm_L4_12_invalid_stored_min_delta_is_not_judged():
    goal = goal_from(make_take(4)).model_copy(update={"min_delta": 0})
    j = judge(goal, make_take(5))
    assert (j.status, j.terminal, j.reason_code) == ("UNKNOWN", False, "INVALID_DATA")
    rng_goal = goal_from(make_take(4), "cpm").model_copy(
        update={"base": goal_from(make_take(4), "cpm").base.model_copy(update={"range": None})}
    )
    assert judge(rng_goal, make_take(5)).reason_code == "INVALID_DATA"


def verify_computed(reg, ev) -> bool:
    """검사기가 하듯 입력 근거만으로 계산 근거의 값을 다시 계산해 본다(L4-25)."""
    ins = [reg.get(i) for i in ev.inputs]
    by_path = {e.path.rsplit(".", 1)[-1]: e for e in ins if e.origin == "RECORD"}
    if ev.calc == "DIFF":
        return math.isclose(ev.value, ins[1].value - ins[0].value, abs_tol=1e-12)
    if ev.calc == "DISTANCE":
        value = ins[0].value
        low = high = None
        for e in ins[1:]:
            name = e.path.rsplit(".", 2)[-2:]
            leaf = ".".join(name)
            if "PACE_SLOW" in leaf or leaf.endswith("min_ms"):
                low = e.value
            elif "PACE_FAST" in leaf or leaf.endswith("max_ms"):
                high = e.value
            else:  # target_ms: 아래 = 위
                low = high = e.value
        return ev.value == max(
            (low if low is not None else -math.inf) - value,
            value - (high if high is not None else math.inf),
            0,
        )
    if ev.calc == "GOAL_DELTA":
        cur, base = ins[0].value, by_path["value"].value
        op = by_path["operator"].value  # 지표 이름이 아니라 입력 근거의 operator 로 방향을 정한다
        return math.isclose(
            ev.value, (cur - base) if op == "gt_base" else (base - cur), abs_tol=1e-12
        )
    if ev.calc == "GOAL_DISTANCE":
        cur = ins[0].value
        low, high = by_path.get("low"), by_path.get("high")
        return ev.value == max(
            (low.value if low else -math.inf) - cur, cur - (high.value if high else math.inf), 0
        )
    if ev.calc == "GOAL_RESULT":
        amount, min_delta = (
            ins[0].value,
            (by_path["min_delta"].value if "min_delta" in by_path else None),
        )
        reg_amount = reg.get(ev.inputs[0])
        achieved = (
            reg_amount.value == 0 if min_delta is None else reg_amount.value + 1e-9 >= min_delta
        )
        return (
            ev.value == ("ACHIEVED" if achieved else "NOT_ACHIEVED") and amount == reg_amount.value
        )
    if ev.calc == "OUTCOME":
        return True
    raise AssertionError(ev.calc)


def test_goal_judgement_evidence_is_recomputable_from_inputs():
    reg = new_registry()
    goal = goal_from(make_take(4, audience_ratio=0.28))
    j = judge(goal, make_take(5, audience_ratio=0.33), reg=reg)
    evs = [reg.get(i) for i in j.evidence_ids]
    kinds = [(e.origin, e.kind or e.calc) for e in evs]
    assert kinds == [
        ("RECORD", "TAKE_RESULT"),
        ("RECORD", "GOAL"),
        ("RECORD", "GOAL"),
        ("RECORD", "GOAL"),
        ("COMPUTED", "GOAL_DELTA"),
        ("COMPUTED", "GOAL_RESULT"),
    ]
    paths = [e.path for e in evs[1:4]]
    assert paths == [
        "conversation_goal.v1.audience_ratio.operator",
        "conversation_goal.v1.audience_ratio.base.value",
        "conversation_goal.v1.audience_ratio.min_delta",
    ]
    assert [e.value for e in evs[1:4]] == [
        "gt_base",
        0.28,
        0.05,
    ]  # 고정한 조건(방향 포함)이 근거에 있다
    assert evs[4].value == pytest.approx(0.05) and evs[5].value == "ACHIEVED"
    for ev in evs:
        if ev.origin == "COMPUTED":
            assert verify_computed(reg, ev), ev.calc
    # 계산 근거를 변조하면 다시 계산이 어긋난다
    tampered = evs[4].model_copy(update={"value": 0.5})
    assert not verify_computed(reg, tampered)


def test_goal_evidence_of_different_goals_does_not_collide():
    reg = new_registry()
    take = make_take(5, audience_ratio=0.33)
    g1 = goal_from(make_take(4, audience_ratio=0.28))
    g2 = goal_from(make_take(3, audience_ratio=0.20)).model_copy(update={"version": 2})
    j1, j2 = judge(g1, take, reg=reg), judge(g2, take, reg=reg)
    assert set(j1.evidence_ids[1:]).isdisjoint(j2.evidence_ids[1:])  # 현재 값 근거만 공유
    assert j1.evidence_ids[0] == j2.evidence_ids[0]
    assert reg.get(j1.evidence_ids[-1]).value == "ACHIEVED"
    assert reg.get(j2.evidence_ids[-1]).value == "ACHIEVED"
    # 같은 값이라도 목표 판(version)이 다르면 섞이지 않는다
    g1b = g1.model_copy(update={"version": 3})
    j1b = judge(g1b, take, reg=reg)
    assert set(j1b.evidence_ids[1:]).isdisjoint(j1.evidence_ids[1:])


def test_goal_in_range_evidence_has_range_bounds_as_inputs():
    reg = new_registry()
    goal = goal_from(make_take(4, cpm=400), "cpm")
    j = judge(goal, make_take(5, cpm=360), reg=reg)
    evs = [reg.get(i) for i in j.evidence_ids]
    assert [e.path.rsplit(".", 1)[-1] for e in evs if e.kind == "GOAL"] == [
        "operator",
        "low",
        "high",
    ]
    dist = next(e for e in evs if e.calc == "GOAL_DISTANCE")
    assert dist.value == 10 and verify_computed(reg, dist)
    result = next(e for e in evs if e.calc == "GOAL_RESULT")
    assert result.value == "NOT_ACHIEVED" and verify_computed(reg, result)


@pytest.mark.parametrize(
    ("take_kw", "reason"),
    [
        ({"script_version_id": "sv-2"}, "DIFFERENT_SCRIPT_VERSION"),
        ({"standard_version": 2}, "DIFFERENT_CRITERIA_VERSION"),
        ({"standard_version": None}, "DIFFERENT_CRITERIA_VERSION"),
        ({"criteria_versions": {"GAZE": "gaze-1.4+zzzzzzzzzzzz"}}, "RULE_VERSION_CHANGED"),
    ],
)
def test_baseline_mismatch_is_terminal(take_kw, reason):
    goal = goal_from(make_take(4))
    j = judge(goal, make_take(5, **take_kw))
    assert (j.status, j.terminal, j.reason_code) == ("BASELINE_MISMATCH", True, reason)


def test_baseline_mismatch_comes_before_other_unknowns():
    goal = goal_from(make_take(4))
    # 버전도 다르고 미완료이고 값도 없음 → 버전 차이가 먼저
    take = make_take(5, standard_version=2, completed_fully=False, audience_ratio=None)
    assert judge(goal, take).status == "BASELINE_MISMATCH"
    # 값이 손상돼 있어도 지표 판정 기준 버전이 다르면 불일치로 끝낸다 (INVALID_DATA 는 버전이 같을 때)
    bad = set_value(
        make_take(5, criteria_versions={"GAZE": "gaze-1.4+zzzzzzzzzzzz"}),
        "GAZE",
        "audience_ratio",
        9,
    )
    assert judge(goal, bad).status == "BASELINE_MISMATCH"


def test_missing_metric_rule_version_is_invalid_data_not_mismatch():
    goal = goal_from(make_take(4))
    j = judge(goal, make_take(5, criteria_versions={"GAZE": None}))
    assert (j.status, j.terminal, j.reason_code) == ("UNKNOWN", False, "INVALID_DATA")


@pytest.mark.parametrize(
    ("take_kw", "reason"),
    [
        ({"completed_fully": False}, "NOT_COMPLETED_FULLY"),
        ({"completed_fully": None}, "NOT_COMPLETED_FULLY"),
        ({"audience_ratio": None}, "NO_VALUE"),
        ({"unmeasured": {"GAZE": "TRACKING_LOST"}}, "NO_VALUE"),
        ({"with_take_result": False}, "NOT_LOADED"),
        ({"omitted_paths": ["take_result.areas.GAZE"]}, "OMITTED"),
    ],
)
def test_unknown_waits_for_next_take(take_kw, reason):
    j = judge(goal_from(make_take(4)), make_take(5, **take_kw))
    assert (j.status, j.terminal, j.reason_code, j.observed) == ("UNKNOWN", False, reason, None)


@pytest.mark.parametrize("bad", ["x", True, math.nan, 2.0, -1])
def test_fm_L4_06_invalid_value_does_not_enter_goal_judgement(bad):
    take = set_value(make_take(5), "GAZE", "audience_ratio", bad)
    j = judge(goal_from(make_take(4)), take)
    assert (j.status, j.terminal, j.reason_code) == ("UNKNOWN", False, "INVALID_DATA")


def test_goal_slide_scope_uses_slide_value():
    goal = goal_from(make_take(4, gaze_slides={3: 0.2}), slide=3)
    cur = make_take(5, audience_ratio=0.9, gaze_slides={3: 0.26})
    assert judge(goal, cur).status == "ACHIEVED"


def test_missing_script_version_is_invalid_data_never_mismatch():
    goal = goal_from(make_take(4))
    for take in (make_take(5, script_version_id=None), make_take(5, script_version_id="")):
        j = judge(goal, take)
        assert (j.status, j.terminal, j.reason_code) == ("UNKNOWN", False, "INVALID_DATA")
    no_script_goal = goal.model_copy(update={"script_version_id": None})
    j = judge(
        no_script_goal, make_take(5, script_version_id=None)
    )  # 양쪽이 모두 없어도 정상 판정으로 가지 않는다
    assert (j.status, j.reason_code) == ("UNKNOWN", "INVALID_DATA")
    # 대본 버전이 없는 기록으로는 목표 초안을 만들지 않는다
    assert draft(make_take(4, script_version_id=None), "audience_ratio") == "NO_VERSION"
    # standard_version 은 null 도 하나의 값이다 (양쪽 null 이면 같음)
    g_null = goal_from(make_take(4, standard_version=None))
    assert (
        judge(g_null, make_take(5, standard_version=None, audience_ratio=0.5)).status == "ACHIEVED"
    )


def test_metric_rule_version_is_checked_before_value_omission():
    goal = goal_from(make_take(4))
    changed = make_take(
        5,
        criteria_versions={"GAZE": "gaze-1.4+zzzzzzzzzzzz"},
        omitted_paths=["take_result.areas.GAZE.take.audience_ratio"],
    )
    j = judge(goal, changed)
    assert (j.status, j.terminal, j.reason_code) == (
        "BASELINE_MISMATCH",
        True,
        "RULE_VERSION_CHANGED",
    )
    same_version = make_take(5, omitted_paths=["take_result.areas.GAZE.take.audience_ratio"])
    j = judge(goal, same_version)
    assert (j.status, j.terminal, j.reason_code) == ("UNKNOWN", False, "OMITTED")
    # 판정 기준 버전 경로 자체가 생략되면 읽지 못한 것이다
    both = make_take(5, omitted_paths=["take_result.areas.GAZE", "take_result.criteria.GAZE"])
    assert judge(goal, both).reason_code == "OMITTED"


# ── 범위 경계 근거 · 생략 · 손상 (L4-25 · L4-05 · L4-06) ───────────────────────────


def test_fm_L4_25_distance_evidence_inputs_include_range_bounds_and_recompute():
    reg = new_registry()
    c = cmp(make_take(4, cpm=420), make_take(5, cpm=380), "cpm", reg=reg)
    evs = [reg.get(i) for i in c.evidence_ids]
    bounds = [e for e in evs if e.origin == "RECORD" and "criteria" in e.path]
    assert sorted(e.value for e in bounds) == [275, 275, 350, 350]
    assert {e.path.rsplit(".", 2)[-2] for e in bounds} == {"PACE_SLOW", "PACE_FAST"}
    for dist in (e for e in evs if e.calc == "DISTANCE"):
        assert len(dist.inputs) == 3 and verify_computed(reg, dist)
    d2 = cmp(
        make_take(4, duration_ms=360000.0),
        make_take(5, duration_ms=340000.0),
        "duration_ms",
        reg=new_registry(),
    )
    assert d2.outcome == "IMPROVED"
    reg3 = new_registry()
    target = cmp(
        make_take(4, plan={"target_ms": 300000}, duration_ms=250000.0),
        make_take(5, plan={"target_ms": 300000}, duration_ms=280000.0),
        "duration_ms",
        reg=reg3,
    )
    dists = [reg3.get(i) for i in target.evidence_ids if reg3.get(i).calc == "DISTANCE"]
    assert [d.value for d in dists] == [50000, 20000] and all(
        verify_computed(reg3, d) for d in dists
    )


def test_fm_L4_25_independent_literals_for_difference_and_tampering():
    reg = new_registry()
    c = cmp(
        make_take(4, audience_ratio=0.28),
        make_take(5, audience_ratio=0.33),
        "audience_ratio",
        reg=reg,
    )
    evs = {e.calc or e.take_number: e for e in (reg.get(i) for i in c.evidence_ids)}
    assert (evs[4].value, evs[5].value) == (
        0.28,
        0.33,
    )  # 고정한 입력 값 (생성된 값끼리 빼서 기대하지 않는다)
    assert evs["DIFF"].value == pytest.approx(0.05)  # 5%p 이지 5% 도 0.5 도 아니다
    assert verify_computed(reg, evs["DIFF"])
    for wrong in (5, 0.5, 0.28 + 0.33, -0.05):  # %/%p 혼동 · 다른 값 섞기
        assert not verify_computed(reg, evs["DIFF"].model_copy(update={"value": wrong}))
    other = cmp(
        make_take(4, audience_ratio=0.28),
        make_take(6, audience_ratio=0.40),
        "audience_ratio",
        reg=reg,
    )
    mixed = reg.get(next(i for i in other.evidence_ids if reg.get(i).calc == "DIFF"))
    assert mixed.value == pytest.approx(0.12) and mixed.evidence_id != evs["DIFF"].evidence_id


def test_fm_L4_05_omitted_range_paths_block_the_comparison():
    plan_omitted = make_take(5, omitted_paths=["take_result.plan"])
    assert cmp(make_take(4), plan_omitted, "duration_ms").reason == "OMITTED"
    pace_omitted = make_take(5, omitted_paths=["take_result.criteria.SPEED.issues"])
    assert cmp(make_take(4), pace_omitted, "cpm").reason == "OMITTED"
    slide_omitted = make_take(
        5, omitted_paths=["take_result.areas.TIME.slides[slide_number=3].target_ms"]
    )
    assert cmp(make_take(4), slide_omitted, "slide_duration_ms", 3).reason == "OMITTED"
    assert draft(plan_omitted, "duration_ms") == "NO_VALUE"
    # 채점 비율 경로가 생략되면 내용 전달 값도 읽지 않는다
    a = make_take(4, scored_ratio=0.9)
    b = make_take(5, scored_ratio=0.9, omitted_paths=["script_coverage.take_scores.scored_ratio"])
    assert cmp(a, b, "content_coverage").reason == "OMITTED"


def test_fm_L4_06_corrupt_range_sources_are_invalid_data_not_exceptions():
    a = make_take(4)
    for mutate in (
        lambda t: t.results.take_result["criteria"]["SPEED"].__setitem__("issues", "oops"),
        lambda t: t.results.take_result["criteria"]["SPEED"]["issues"].__setitem__(
            "PACE_FAST", 350
        ),
    ):
        b = make_take(5)
        mutate(b)
        assert cmp(a, b, "cpm").reason == "INVALID_DATA"
    p = make_take(5)
    p.results.take_result["plan"] = [1, 2]
    assert cmp(a, p, "duration_ms").reason == "INVALID_DATA"
    t = make_take(5)
    t.results.take_result["areas"]["TIME"]["slides"] = "x"
    assert cmp(a, t, "slide_duration_ms", 3).reason == "INVALID_DATA"
    assert draft(p, "duration_ms") == "INVALID_DATA"


def test_goal_evidence_hangs_on_the_base_take_and_is_dropped_with_it():
    reg = new_registry()
    goal = goal_from(make_take(4, audience_ratio=0.28))
    j = judge(goal, make_take(5, audience_ratio=0.33), reg=reg)
    goal_recs = [reg.get(i) for i in j.evidence_ids if reg.get(i).kind == "GOAL"]
    assert goal_recs and all((e.take_id, e.take_number) == ("take-4", 4) for e in goal_recs)
    assert all(reg.is_current(i) for i in j.evidence_ids)
    gone = reg.drop_sources(["take-4"])
    # 기준값 · 조건 근거와 그것을 입력으로 쓴 계산 근거(GOAL_DELTA · GOAL_RESULT)가 함께 무효다. 현재 Take 의 값은 남는다
    assert {e.evidence_id for e in goal_recs} <= gone
    computed = [i for i in j.evidence_ids if i.startswith("c-")]
    assert len(computed) == 2 and set(computed) <= gone
    assert not any(reg.is_current(i) for i in computed)
    assert reg.is_current(j.evidence_ids[0])


def test_goal_delta_direction_is_recomputed_from_the_operator_input_only():
    reg = new_registry()
    # 지표 이름이 아니라 입력 근거의 operator 가 방향을 정한다
    goal = goal_from(make_take(4, filler_per_min=2.0), "filler_per_min")
    j = judge(goal, make_take(5, filler_per_min=1.5), reg=reg)
    evs = [reg.get(i) for i in j.evidence_ids]
    op = next(e for e in evs if e.path.endswith(".operator"))
    delta = next(e for e in evs if e.calc == "GOAL_DELTA")
    assert op.evidence_id in delta.inputs and op.value == "lt_base"
    assert delta.value == pytest.approx(0.5) and verify_computed(reg, delta)
    reg.items[op.evidence_id] = op.model_copy(update={"value": "gt_base"})
    assert not verify_computed(reg, delta)  # operator 를 바꾸면 방향이 뒤집혀 값이 맞지 않는다


def test_fm_L4_05_omitted_descendant_paths_block_range_sources():
    from pitch_coach_ai.features.chat_agent.values import OmittedDataError, range_sources

    cpm = make_take(5, omitted_paths=["take_result.criteria.SPEED.issues.PACE_FAST.threshold"])
    with pytest.raises(OmittedDataError):
        range_sources(cpm, "cpm", None, CFG)  # payload 에 옛 경계 350 이 남아 있어도 쓰지 않는다
    assert cmp(make_take(4), cpm, "cpm").reason == "OMITTED"
    plan = make_take(5, omitted_paths=["take_result.plan.max_ms"])
    assert cmp(make_take(4), plan, "duration_ms").reason == "OMITTED"
    # 비슷한 이름(PACE 와 PACE_FAST) 은 조상 · 자손이 아니다
    similar = make_take(5, omitted_paths=["take_result.criteria.SPEED.issues.PACE"])
    assert range_sources(similar, "cpm", None, CFG)[0] == (275.0, 350.0)
    other = make_take(5, omitted_paths=["take_result.plan.max_ms_extra"])
    assert cmp(make_take(4), other, "duration_ms").comparable
