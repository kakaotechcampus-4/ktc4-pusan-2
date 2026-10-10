"""실험 장치와 성능 하한선.

하한선은 실험(reports/results/take_result.json)에서 잰 값보다 조금 낮게 둡니다.
규칙을 고쳐 Take 결과가 나빠지면 여기서 걸립니다. seed 2개만 돌려 빠르게 보므로
하한선은 seed 5개 결과가 아니라 이 테스트의 seed 2개 값(noisy) 기준으로 여유를 둡니다.
"""

from __future__ import annotations

import pytest

from coach_lab.evaluate import DEFAULT_VARIANT, claimed_segments, evaluate, rebuild_take
from coach_lab.paths import SCENARIOS_DIR
from coach_lab.simulator import NOISE_PRESETS, Scenario, run, scenario_config
from coach_lab.truth import truth_intervals

SCENARIOS = [Scenario.load(p) for p in sorted(SCENARIOS_DIR.glob("*.json"))]

#: 구간을 합치지도 짧은 구간을 빼지도 않는 변형
NO_MERGE = "합치기 없음"
VARIANTS = {
    DEFAULT_VARIANT: {},
    NO_MERGE: {"take_result": {"merge_gap_ms": 0, "min_segment_ms": 0}},
}


def _sc(name: str) -> Scenario:
    return next(s for s in SCENARIOS if s.name == name)


def test_noise_is_reproducible_by_seed():
    sc = _sc("06_pace_fast")
    a = run(sc, noise=NOISE_PRESETS["noisy"], seed=7)
    b = run(sc, noise=NOISE_PRESETS["noisy"], seed=7)
    c = run(sc, noise=NOISE_PRESETS["noisy"], seed=8)
    assert a.events == b.events
    assert a.events != c.events


def test_a_good_presenter_has_no_truth_and_no_claims():
    # 전달 습관에 문제가 없는 발표자는 정답에도 Take 결과에도 문제 구간이 없다
    result = run(_sc("01_baseline_good"))
    assert truth_intervals(result) == []
    assert claimed_segments(result.take_result, result.config) == []


def test_truth_marks_unobservable_problems():
    result = run(_sc("16_noisy_sensors"))
    seen = {(i.area.value, i.observable) for i in truth_intervals(result)}
    assert ("GAZE", False) in seen and ("SPEED", False) in seen and ("VOLUME", True) in seen


def test_rebuilding_with_the_same_config_gives_the_same_take_result():
    result = run(_sc("14_recurring_persists"))
    assert rebuild_take(result, result.config) == result.take_result


def test_rebuilding_with_another_config_changes_only_the_segments():
    result = run(_sc("07_exam_mode"))
    strict = scenario_config(result.scenario, {"take_result": {"min_segment_ms": 60_000}})
    rebuilt = rebuild_take(result, strict)
    assert len(rebuilt.problem_segments) < len(result.take_result.problem_segments)
    assert rebuilt.areas == result.take_result.areas
    assert rebuilt.interventions == result.take_result.interventions


@pytest.fixture(scope="module")
def noisy_scores():
    return evaluate(SCENARIOS, ["noisy"], 2, VARIANTS)["noisy"]


def test_merging_pieces_reduces_fragments(noisy_scores):
    merged = noisy_scores[DEFAULT_VARIANT].summary()
    split = noisy_scores[NO_MERGE].summary()
    assert merged["fragments_per_problem"] < split["fragments_per_problem"]


def test_quality_floor_on_noisy_data(noisy_scores):
    # seed 2개의 noisy 결과(재현율 · 정밀도 0.917, IoU 0.78, 시작 오차 1.6초, CPM 오차 6.1 ...)
    # 보다 조금 낮게 둔다
    m = noisy_scores[DEFAULT_VARIANT].summary()
    assert m["segment_recall"] >= 0.85
    assert m["segment_precision"] >= 0.85
    assert m["segment_f1"] >= 0.88
    assert m["unsupported_claims_per_take"] <= 0.12
    assert m["segment_iou"] >= 0.7
    assert m["onset_error_s"] <= 3.0
    assert m["slide_mae"]["script_ratio"] <= 0.1
    assert m["slide_mae"]["cpm"] <= 9
    assert m["slide_mae"]["duration_ms"] <= 1.0
    assert m["outcome_accuracy"] >= 0.9
    assert m["false_intervention_rate"] <= 0.1
