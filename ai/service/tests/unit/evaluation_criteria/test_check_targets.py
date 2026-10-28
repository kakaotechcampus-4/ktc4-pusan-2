"""숫자 목표 검사 check_targets (evaluation-criteria.md 6절 2번). LLM 없음."""

import pytest

from pitch_coach_ai.features.evaluation_criteria import check_targets
from pitch_coach_ai.features.evaluation_criteria.config import EvaluationCriteriaConfig
from pitch_coach_ai.features.evaluation_criteria.schemas import Target

DISPLAY = "기준"


def target(metric="filler_count", operator="LTE", value=5, display=DISPLAY) -> Target:
    return Target(display=display, metric=metric, operator=operator, value=value)


def kept(*targets: Target, display_criteria=(DISPLAY,), config=None) -> list[Target]:
    args = (list(targets), list(display_criteria))
    return check_targets(*args, config) if config else check_targets(*args)


def test_valid_targets_are_kept_in_order():
    a = target("cpm", "LTE", 300)
    b = target("audience_ratio", "GTE", 0.7)
    assert kept(a, b) == [a, b]


# ── 지표 · 연산자 enum ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field, bad", [("metric", "speed_score"), ("operator", "EQ")], ids=["metric", "operator"]
)
def test_unknown_metric_or_operator_is_dropped(field, bad):
    # 스키마가 막지만, 검증 없이 만들어진 목표도 다시 확인한다
    raw = target().model_dump() | {field: bad}
    assert kept(Target.model_construct(**raw)) == []


# ── 값 범위 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("metric", ["audience_ratio", "reading_ratio", "content_coverage"])
@pytest.mark.parametrize(
    "value, ok", [(0, True), (1, True), (0.7, True), (-0.1, False), (1.2, False), (70, False)]
)
def test_ratio_metrics_are_between_0_and_1(metric, value, ok):
    assert bool(kept(target(metric, "GTE", value))) is ok


@pytest.mark.parametrize(
    "metric", ["pause_count", "long_silence_count", "filler_count", "filler_per_min"]
)
@pytest.mark.parametrize("value, ok", [(0, True), (3, True), (-1, False)])
def test_count_metrics_are_not_negative(metric, value, ok):
    assert bool(kept(target(metric, "LTE", value))) is ok


@pytest.mark.parametrize("value, ok", [(300, True), (0, False), (-10, False)])
def test_cpm_is_positive(value, ok):
    assert bool(kept(target("cpm", "LTE", value))) is ok


def test_voice_diff_db_may_be_negative():
    # 평소 목소리 대비라 작게 말하기 기준은 음수다
    assert kept(target("voice_diff_db", "LTE", -3))


# ── display 짝 ────────────────────────────────────────────────────────────────


def test_target_without_matching_display_is_dropped():
    assert kept(target(display="다른 기준")) == []


def test_display_must_match_exactly():
    assert kept(target(display="추임새 5번 이하 "), display_criteria=["추임새 5번 이하"]) == []


def test_display_criteria_are_not_changed():
    display_criteria = ["a", "b"]
    check_targets([target(display="x")], display_criteria)
    assert display_criteria == ["a", "b"]


# ── 중복 · 개수 ───────────────────────────────────────────────────────────────


def test_duplicate_display_metric_operator_keeps_first():
    first = target(value=5)
    assert kept(first, target(value=3)) == [first]


def test_range_is_two_targets_with_the_same_display():
    low, high = target("cpm", "GTE", 250), target("cpm", "LTE", 350)
    assert kept(low, high) == [low, high]


def test_more_than_max_targets_keeps_the_first(caplog):
    displays = [f"기준 {i}" for i in range(25)]
    targets = [target(value=i, display=d) for i, d in enumerate(displays)]
    with caplog.at_level("INFO"):
        result = kept(*targets, display_criteria=displays)
    assert result == targets[:20]
    assert caplog.records, "뺀 수를 로그에 남긴다"


def test_max_targets_comes_from_config():
    displays = [f"기준 {i}" for i in range(5)]
    targets = [target(display=d) for d in displays]
    config = EvaluationCriteriaConfig(max_targets=2)
    assert kept(*targets, display_criteria=displays, config=config) == targets[:2]


def test_cap_is_applied_after_filtering():
    displays = [f"기준 {i}" for i in range(21)]
    bad = target("cpm", "LTE", 0, display=displays[0])
    good = [target(display=d) for d in displays[1:]]
    assert kept(bad, *good, display_criteria=displays) == good
