"""8단계 반복 문제 · 9단계 점수 · 기본 문장 · review_take 전체."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from review import ReviewRequest, load_config, review_take
from review.schemas import NextMission, RecurringIssue
from review.score import compute_score
from review.text import NOTHING_TEXT, STABLE_TEXT, TextCandidates, default_summary
from review.vocab import DIMENSIONS

from .conftest import run, segment, unmeasured


def test_sample_recurring_issues(req):
    resp, _ = run(req)
    assert [r.model_dump() for r in resp.recurring_issues] == [
        {"area": "GAZE", "slide_number": 3, "dimension": "script_independence"},
        {"area": "FILLER", "slide_number": 2, "dimension": "fluency"},
    ]


def test_pacing_recurring_area_is_largest_burden_area(req):
    req["take_result"]["problem_segments"] += [
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8),  # 12.0
        segment("PACE_FAST", "SPEED", 1, 5_000, 10_000, 0.5),  # 2.5
    ]
    resp, _ = run(req)
    pacing = [r for r in resp.recurring_issues if r.dimension == "pacing_rhythm"]
    assert [(r.area, r.slide_number) for r in pacing] == [("TIME", 3)]


def test_recurring_slide_is_concentrated_within_chosen_area(req):
    # 차원 전체로는 3번 장이 12 / 24 = 0.5 로 몰리지 않았지만,
    # 고른 영역 TIME 에서는 3번 장에 모두 몰렸다
    req["take_result"]["problem_segments"] += [
        segment("SLIDE_OVER", "TIME", 3, 110_000, 125_000, 0.8),  # 12.0
        segment("PACE_FAST", "SPEED", 1, 0, 6_000, 0.5),  # 3.0
        segment("PACE_FAST", "SPEED", 2, 0, 6_000, 0.5),  # 3.0
        segment("LONG_SILENCE", "PAUSE", 4, 0, 10_000, 0.6),  # 6.0
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


def _gaze_slides(*metrics: dict | None) -> list[dict]:
    """1번 장부터 GAZE 장 값 (None 이면 측정 비율 미달로 metrics 가 null 인 장)"""
    return [
        {"slide_number": n, "measured_ratio": 0.9 if m else 0.1, "metrics": m}
        for n, m in enumerate(metrics, 1)
    ]


def _strength(resp, dimension_area: str):
    return next(s for s in resp.strengths if s.area == dimension_area)


def test_strength_slide_is_null_even_with_slide_values(req):
    # STRENGTH 는 그 차원의 Take 전체에 문제가 없다는 뜻
    # → 장 값이 있어도 장 번호 null, 근거는 Take 값
    req["take_result"]["areas"]["GAZE"]["slides"] = _gaze_slides(
        {"audience_ratio": 0.7},
        {"audience_ratio": 0.9},
        {"audience_ratio": 0.5, "script_ratio": 0.62},
    )
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "STRENGTH"
    s = _strength(resp, "GAZE")
    assert (s.slide_number, s.evidence) == (None, {"audience_ratio": 0.58})


def test_improved_strength_slide_is_achieved_slide_mission(req):
    req["missions"].append(
        {
            "mission_id": "m-gaze-1",
            "area": "GAZE",
            "slide_number": 1,
            "target": {"metric": "audience_ratio", "operator": "GTE", "value": 0.5},
        }
    )
    req["take_result"]["areas"]["GAZE"]["slides"] = _gaze_slides(
        {"audience_ratio": 0.6}, {"audience_ratio": 0.9}, {"script_ratio": 0.62}
    )
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "IMPROVED"  # 미션을 이루고 문제가 없음
    s = _strength(resp, "GAZE")
    assert (s.slide_number, s.evidence) == (1, {"audience_ratio": 0.6})


def test_improved_strength_without_slide_is_null(req):
    # Take 전체의 반복 문제가 나아졌으면 장이 없다
    req["previous_recurring_issues"].append(
        {"area": "GAZE", "slide_number": None, "dimension": "audience_gaze"}
    )
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "IMPROVED"
    s = _strength(resp, "GAZE")
    assert (s.slide_number, s.evidence) == (None, {"audience_ratio": 0.58})


def test_improved_strength_slide_is_previous_recurring_slide(req):
    req["previous_recurring_issues"].append(
        {"area": "GAZE", "slide_number": 1, "dimension": "audience_gaze"}
    )
    req["take_result"]["areas"]["GAZE"]["slides"] = _gaze_slides(
        {"audience_ratio": 0.6},
        {"audience_ratio": 0.9},
        {"audience_ratio": 0.5, "script_ratio": 0.62},
    )
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "IMPROVED"
    s = _strength(resp, "GAZE")
    # 나아진 근거의 장(직전 반복 문제 1번 장)과 그 장 값
    assert (s.slide_number, s.evidence) == (1, {"audience_ratio": 0.6})


def test_improved_strength_unmeasured_slide_is_null(req):
    # 직전 반복 문제의 장을 이번에 재지 못했으면 그 장이 나아졌다고 말하지 않는다
    # → 장 번호 null, 근거는 Take 값
    req["previous_recurring_issues"].append(
        {"area": "GAZE", "slide_number": 3, "dimension": "audience_gaze"}
    )
    req["take_result"]["areas"]["GAZE"]["slides"] = _gaze_slides(
        {"audience_ratio": 0.6}, {"audience_ratio": 0.9}, None
    )
    resp, _ = run(req)
    assert resp.dimensions["audience_gaze"] == "IMPROVED"
    s = _strength(resp, "GAZE")
    assert (s.slide_number, s.evidence) == (None, {"audience_ratio": 0.58})


def test_sample_score_and_text(req):
    resp, _ = run(req)
    assert resp.score == 76  # (100 × 3 + 40 + 40) / 5
    assert resp.overall_summary == (
        "좋았던 점은 청중 응시, 말 속도와 시간 배분, 목소리 크기입니다. "
        "먼저 다듬을 점은 3번 장 대본 의존도, 2번 장 말의 매끄러움입니다."
    )
    imp = resp.improvements[0]
    assert (imp.area, imp.slide_number, imp.evidence) == ("GAZE", 3, {"script_ratio": 0.62})
    assert [s.area for s in resp.strengths] == ["GAZE", "SPEED", "VOLUME"]


@pytest.mark.parametrize(("evaluable", "expected"), [(True, STABLE_TEXT), (False, NOTHING_TEXT)])
def test_default_summary_without_candidates(evaluable, expected):
    # 판단할 수 있는 차원이 모두 STABLE 이면 '측정 부족'이 아니라 '전반적으로 보통'
    assert default_summary(TextCandidates([], [], evaluable)) == expected


def test_all_stable_take_summary(req, monkeypatch):
    # 실제 입력으로 여섯 차원을 모두 STABLE 로 만들기 어려워 상태만 바꿔 본다
    monkeypatch.setattr(
        "review.core.decide_dimension_states",
        lambda *a, **k: dict.fromkeys(DIMENSIONS, "STABLE"),
    )
    resp = review_take(req)
    assert resp.strengths == [] and resp.improvements == []
    assert resp.overall_summary == STABLE_TEXT
    # 예시 요청의 사용자 기준 FAILED 는 다음 미션 1순위로 나가므로
    # 요약이 '고칠 점 없음'이라 하지 않는다
    assert any(r.status == "FAILED" for r in resp.criteria_results)
    assert resp.next_missions[0].priority == 1
    assert "고칠 점" not in resp.overall_summary


def test_score_none_when_nothing_evaluable():
    assert compute_score({d: "NOT_EVALUABLE" for d in DIMENSIONS}, load_config()) is None


def test_score_rounds_half_up():
    # (100 + 85) / 2 = 92.5 → 93 (round() 는 92)
    assert compute_score({"fluency": "STRENGTH", "vocal_delivery": "IMPROVED"}, load_config()) == 93


def test_fallback_and_meta(req):
    resp = review_take(req)
    assert resp.fallback_reason == "LLM_UNAVAILABLE"
    assert resp.comparison == []
    assert resp.meta.schema_version == "1.0"
    assert resp.meta.feature_version == "review-1.2"
    assert resp.meta.criteria_versions["review"].startswith("review-1.2+")
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
    assert review_take(ReviewRequest.model_validate(req)).score == 76


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


def test_criteria_versions_carry_take_result_criteria(req):
    # 한 모듈의 기준이 여러 영역에 실려도(volume → VOLUME · PAUSE) 한 번만
    req["take_result"]["criteria"].update(
        {
            "VOLUME": {"criteria_version": "volume-1.0+aaaaaaaaaaaa", "issues": {}},
            "PAUSE": {"criteria_version": "volume-1.0+aaaaaaaaaaaa", "issues": {}},
            "SPEED": {"issues": {}},  # 버전이 없으면 싣지 않는다
        }
    )
    versions = review_take(req).meta.criteria_versions
    assert versions.keys() == {"gaze", "volume", "review"}
    assert versions["gaze"] == "gaze-1.1+000000000000"
    assert versions["volume"] == "volume-1.0+aaaaaaaaaaaa"
    assert versions["review"].startswith("review-1.2+")


def test_comparison_shape():
    from review.schemas import Comparison

    c = Comparison.model_validate(
        {
            "metric": "cpm",
            "slide_number": None,
            "previous": 300,
            "current": 320,
            "improved": None,
            "script_mode": {"previous": "SCRIPT", "current": "NO_SCRIPT"},
        }
    )
    assert c.improved is None
    assert c.script_mode.current == "NO_SCRIPT"


@pytest.mark.parametrize(
    ("field", "limit"),
    [("previous_takes", 3), ("missions", 20), ("previous_recurring_issues", 20)],
)
def test_input_count_limits(req, field, limit):
    item = {
        "previous_takes": {"take_result": req["take_result"], "take_scores": None},
        "missions": req["missions"][0],
        "previous_recurring_issues": req["previous_recurring_issues"][0],
    }[field]
    req[field] = [item] * limit
    review_take(req)  # 한도까지는 받는다
    req[field] = [item] * (limit + 1)
    with pytest.raises(ValidationError):  # 라우터가 422 로 바꾼다
        review_take(req)


def test_criteria_targets_limit(req):
    t = req["user_criteria"]["targets"][0]
    req["user_criteria"]["targets"] = [t] * 21
    with pytest.raises(ValidationError):
        review_take(req)


@pytest.mark.parametrize("field", ["missions", "targets"])
def test_eq_operator_is_rejected(req, field):
    # 코칭 계획 입력(coach-agent.md 5-2)이 받는 LT · LTE · GT · GTE 만 받는다
    if field == "missions":
        req["missions"][0]["target"]["operator"] = "EQ"
    else:
        req["user_criteria"]["targets"][0]["operator"] = "EQ"
    with pytest.raises(ValidationError):  # 라우터가 422 로 바꾼다
        review_take(req)


def test_mission_slide_number_starts_at_one(req):
    # 장 번호는 1부터, 0을 Take 전체로 쓰지 않는다 (architecture.md 6-1)
    req["missions"][0]["slide_number"] = 0
    with pytest.raises(ValidationError):  # 라우터가 422 로 바꾼다
        review_take(req)


def test_output_mission_and_recurring_contract():
    target = {"metric": "script_ratio", "operator": "LTE", "value": 0.52}
    ok = {"area": "GAZE", "slide_number": 3, "description": None, "target": target}
    NextMission(**ok, priority=1, reason="이유")
    for bad in ({"area": None}, {"area": "EYE"}, {"slide_number": 0}):
        with pytest.raises(ValidationError):
            NextMission(**{**ok, **bad}, priority=1, reason="이유")
    RecurringIssue(area="GAZE", slide_number=None, dimension="script_independence")
    with pytest.raises(ValidationError):
        RecurringIssue(area="GAZE", slide_number=0, dimension="script_independence")
