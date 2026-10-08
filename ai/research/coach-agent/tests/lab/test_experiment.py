"""실험 장치와 성능 하한선.

하한선은 실험(reports/results/review_evidence.json)에서 잰 값보다 조금 낮게 둡니다.
규칙을 고쳐 리뷰 근거가 나빠지면 여기서 걸립니다. seed 2개만 돌려 빠르게 봅니다.
"""

from __future__ import annotations

import pytest

from coach_lab.evaluate import VARIANTS, evaluate
from coach_lab.paths import SCENARIOS_DIR
from coach_lab.simulator import NOISE_PRESETS, Scenario, run
from coach_lab.truth import truth_assessment, truth_intervals

SCENARIOS = [Scenario.load(p) for p in sorted(SCENARIOS_DIR.glob("*.json"))]


def _sc(name: str) -> Scenario:
    return next(s for s in SCENARIOS if s.name == name)


def test_noise_is_reproducible_by_seed():
    sc = _sc("06_pace_fast")
    a = run(sc, noise=NOISE_PRESETS["noisy"], seed=7)
    b = run(sc, noise=NOISE_PRESETS["noisy"], seed=7)
    c = run(sc, noise=NOISE_PRESETS["noisy"], seed=8)
    assert a.events == b.events
    assert a.events != c.events


def test_truth_of_a_good_presenter_matches_the_review():
    # 전달 습관에는 문제가 없다. 다만 계획의 3번 장 필수 키워드를 말하지 않아 내용(CONTENT) 문제만
    # 남는다
    result = run(_sc("01_baseline_good"))
    truth, intervals = truth_assessment(result)
    assert intervals == []
    want = [(i.area.value, i.slide_number) for i in truth.issues]
    assert want == [("CONTENT", 3)]
    assert [(i.area.value, i.slide_number) for i in result.review.issues] == want


def test_truth_marks_unobservable_problems():
    result = run(_sc("16_noisy_sensors"))
    seen = {(i.area.value, i.observable) for i in truth_intervals(result)}
    assert ("GAZE", False) in seen and ("SPEED", False) in seen and ("VOLUME", True) in seen


@pytest.fixture(scope="module")
def noisy_scores():
    return evaluate(SCENARIOS, ["noisy"], 2, VARIANTS)["noisy"]


def test_review_rules_beat_the_first_version(noisy_scores):
    a = noisy_scores["A 기준선(v1.0)"].summary()
    d = noisy_scores["D +지연 보정"].summary()
    assert d["segment_precision"] > a["segment_precision"]
    assert d["segment_iou"] > a["segment_iou"]
    assert d["unsupported_claims_per_take"] < a["unsupported_claims_per_take"]
    assert d["top_issue_type_agreement"] >= a["top_issue_type_agreement"]


def test_quality_floor_on_noisy_data(noisy_scores):
    m = noisy_scores["D +지연 보정"].summary()
    assert m["segment_recall"] >= 0.85
    assert m["segment_precision"] >= 0.93
    assert m["segment_iou"] >= 0.75
    assert m["unsupported_claims_per_take"] <= 0.1
    assert m["mission_accuracy"] >= 0.9
    assert m["type_status_accuracy"] >= 0.95
    assert m["memory_accuracy"] >= 0.95
    assert m["top_issue_type_agreement"] >= 0.8
    assert m["outcome_accuracy"] >= 0.9
    assert m["false_intervention_rate"] <= 0.06
