"""8단계 반복 문제 · 9단계 점수 · 기본 문장 · review_take 전체."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from review import ReviewRequest, load_config, review_take
from review.score import compute_score
from review.vocab import DIMENSIONS

from .conftest import run, segment, unmeasured


def test_sample_recurring_issues(req):
    resp, _ = run(req)
    assert [r.model_dump() for r in resp.recurring_issues] == [
        {"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}
    ]


def test_pacing_recurring_area_is_largest_burden_area(req):
    req["take_result"]["problem_segments"] += [
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8),  # 12.0
        segment("PACE_FAST", "SPEED", 1, 5_000, 10_000, 0.5),  # 2.5
    ]
    resp, _ = run(req)
    pacing = [r for r in resp.recurring_issues if r.dimension == "pacing_rhythm"]
    assert [(r.area, r.slide_number) for r in pacing] == [("TIME", 3)]


def test_improvement_evidence_comes_from_its_area(req):
    req["take_result"]["problem_segments"].append(
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8)
    )
    resp, _ = run(req)
    pacing = [i for i in resp.improvements if i.area == "TIME"]
    assert [i.evidence for i in pacing] == [{"slide_duration_ms": 60000}]


def test_strength_skips_unmeasured_area(req):
    # SPEED · PAUSE 를 못 재고 TIME 만 쟀으면 pacing_rhythm 강점은 TIME 지표로
    for area in ("SPEED", "PAUSE"):
        req["take_result"]["areas"][area] = unmeasured()
    resp, _ = run(req)
    assert resp.dimensions["pacing_rhythm"] == "STRENGTH"
    pacing = [s for s in resp.strengths if s.area in ("SPEED", "PAUSE", "TIME")]
    assert [(s.area, s.evidence) for s in pacing] == [("TIME", {"duration_ms": 182000})]


def test_sample_score_and_text(req):
    resp, _ = run(req)
    assert resp.score == 82  # (100 × 3 + 40 + 70) / 5
    assert resp.overall_summary == (
        "좋았던 점은 청중 응시, 말 속도와 시간 배분, 목소리 크기입니다. "
        "먼저 다듬을 점은 3번 장 대본 의존도입니다."
    )
    imp = resp.improvements[0]
    assert (imp.area, imp.slide_number, imp.evidence) == ("GAZE", 3, {"script_ratio": 0.62})
    assert [s.area for s in resp.strengths] == ["GAZE", "SPEED", "VOLUME"]


def test_score_none_when_nothing_evaluable():
    assert compute_score({d: "NOT_EVALUABLE" for d in DIMENSIONS}, load_config()) is None


def test_fallback_and_meta(req):
    resp = review_take(req)
    assert resp.fallback_reason == "LLM_UNAVAILABLE"
    assert resp.comparison == []
    assert resp.meta.schema_version == "1.0"
    assert resp.meta.feature_version == "review-1.0"
    assert resp.meta.criteria_versions["review"].startswith("review-1.0+")
    assert resp.meta.model is None


def test_same_input_same_output(req):
    assert review_take(req).model_dump() == review_take(req).model_dump()


def test_config_change_changes_criteria_version(req):
    a = review_take(req).meta.criteria_versions["review"]
    b = review_take(
        req, config=load_config(burden={"recurring_weight": 1.5})
    ).meta.criteria_versions["review"]
    assert a != b


def test_accepts_model_and_ignores_unknown_fields(req):
    req["take_result"]["new_field_from_be"] = 1
    req["extra"] = {"x": 1}
    assert review_take(ReviewRequest.model_validate(req)).score == 82


def test_null_lists_are_empty(req):
    req["previous_recurring_issues"] = None
    req["missions"] = None
    req["previous_takes"] = None
    req["user_criteria"] = None
    resp = review_take(req)
    assert resp.mission_results == [] and resp.criteria_results == []


def test_bad_request_raises_validation_error(req):
    del req["take_result"]["take_id"]
    with pytest.raises(ValidationError):
        review_take(req)
