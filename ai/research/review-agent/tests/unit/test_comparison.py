"""6단계 이전 Take 비교."""

from __future__ import annotations

import copy

from .conftest import run, sample_coverage, unmeasured


def previous_take(req, take_scores=None):
    """이번 Take 결과를 복사한 이전 Take. 테스트가 바꿀 값만 고친다"""
    return {"take_result": copy.deepcopy(req["take_result"]), "take_scores": take_scores}


def by_key(resp):
    return {(c.metric, c.slide_number): c for c in resp.comparison}


def test_no_previous_take_no_comparison(req):
    req["previous_takes"] = []
    resp, _ = run(req)
    assert resp.comparison == []


def test_one_direction_metrics(req):
    prev = previous_take(req)
    gaze = prev["take_result"]["areas"]["GAZE"]
    gaze["take"]["audience_ratio"] = 0.60  # 이번 0.58: 낮아짐 → 나빠짐
    gaze["take"]["script_ratio"] = 0.40  # 이번 0.31: 낮아짐 → 좋아짐
    gaze["slides"][0]["metrics"]["script_ratio"] = 0.70  # 3번 장 이번 0.62
    req["previous_takes"] = [prev]
    c = by_key(run(req)[0])

    assert (c[("audience_ratio", None)].previous, c[("audience_ratio", None)].improved) == (
        0.60,
        False,
    )
    assert c[("script_ratio", None)].improved is True
    slide = c[("script_ratio", 3)]
    assert (slide.previous, slide.current, slide.improved) == (0.70, 0.62, True)
    assert c[("reading_runs", None)].improved is False  # 값이 같으면 좋아지지 않음
    assert c[("pause_count", None)].improved is None  # 방향을 정하지 않은 지표


def test_order_follows_dimension_table(req):
    req["previous_takes"] = [previous_take(req)]
    resp, _ = run(req)
    assert [(c.metric, c.slide_number) for c in resp.comparison][:4] == [
        ("audience_ratio", None),
        ("script_ratio", None),
        ("script_ratio", 3),
        ("reading_ratio", None),
    ]


def test_only_metrics_in_direction_table(req):
    req["previous_takes"] = [previous_take(req)]
    metrics = {c.metric for c in run(req)[0].comparison}
    assert "level_db" not in metrics  # 표에 없는 지표
    assert "by_tier" not in metrics  # 숫자가 아닌 값


def test_skip_when_one_side_is_unmeasured(req):
    prev = previous_take(req)
    prev["take_result"]["areas"]["FILLER"] = unmeasured(0.2)
    req["previous_takes"] = [prev]
    metrics = {c.metric for c in run(req)[0].comparison}
    assert not metrics & {"filler_count", "filler_per_min"}


def test_script_mode_difference(req):
    prev = previous_take(req)
    req["previous_takes"] = [prev]
    assert all(c.script_mode is None for c in run(req)[0].comparison)

    prev["take_result"]["script_mode"] = "OFF"
    resp, _ = run(req)
    assert resp.comparison[0].script_mode.model_dump() == {
        "previous": "OFF",
        "current": "HIGHLIGHT",
    }


def _speed_criteria(slow, fast):
    return {
        "criteria_version": "pace-1.0+000000000000",
        "issues": {
            "PACE_SLOW": {"metric": "cpm", "direction": "LOWER_IS_WORSE", "threshold": slow},
            "PACE_FAST": {"metric": "cpm", "direction": "HIGHER_IS_WORSE", "threshold": fast},
        },
    }


def test_cpm_closer_to_each_takes_range(req):
    prev = previous_take(req)
    prev["take_result"]["areas"]["SPEED"]["take"]["cpm"] = 340  # 범위 밖 40
    prev["take_result"]["criteria"]["SPEED"] = _speed_criteria(250, 300)
    req["take_result"]["criteria"]["SPEED"] = _speed_criteria(250, 300)  # 이번 318.5: 밖 18.5
    req["previous_takes"] = [prev]
    assert by_key(run(req)[0])[("cpm", None)].improved is True

    del prev["take_result"]["criteria"]["SPEED"]  # 한쪽 기준이 없으면 방향을 정할 수 없다
    assert by_key(run(req)[0])[("cpm", None)].improved is None


def test_duration_uses_plan_range_or_target(req):
    prev = previous_take(req)
    prev["take_result"]["areas"]["TIME"]["take"]["duration_ms"] = 200_000
    prev["take_result"]["plan"] = {"target_ms": 180_000, "min_ms": 170_000, "max_ms": 190_000}
    req["take_result"]["plan"] = {"target_ms": 180_000, "min_ms": 170_000, "max_ms": 190_000}
    req["previous_takes"] = [prev]
    assert by_key(run(req)[0])[("duration_ms", None)].improved is True  # 10초 밖 → 범위 안

    # min · max 가 없으면 target_ms 하나가 기준: 이전 20초 차이, 이번 2초 차이
    for take in (prev["take_result"], req["take_result"]):
        take["plan"] = {"target_ms": 180_000, "min_ms": None, "max_ms": None}
    assert by_key(run(req)[0])[("duration_ms", None)].improved is True

    req["take_result"]["plan"] = None
    assert by_key(run(req)[0])[("duration_ms", None)].improved is None


def test_slide_duration_uses_each_slide_target(req):
    prev = previous_take(req)
    slide = prev["take_result"]["areas"]["TIME"]["slides"][0]
    slide["target_ms"], slide["metrics"]["slide_duration_ms"] = 50_000, 70_000  # 20초 넘음
    req["previous_takes"] = [prev]  # 이번 3번 장 목표 60초에 60초
    assert by_key(run(req)[0])[("slide_duration_ms", 3)].improved is True


def test_content_take_scores(req):
    req["script_coverage"] = sample_coverage()  # 이번 content_coverage 1.0
    prev = previous_take(req, {"content_coverage": 0.8, "scored_ratio": 1.0})
    req["previous_takes"] = [prev]
    c = by_key(run(req)[0])[("content_coverage", None)]
    assert (c.previous, c.current, c.improved) == (0.8, 1.0, True)

    # 일부 장만 채점된 Take 의 점수는 비교하지 않는다
    prev["take_scores"]["scored_ratio"] = 0.3
    assert ("content_coverage", None) not in by_key(run(req)[0])


def test_only_previous_takes_first_is_compared(req):
    first, older = previous_take(req), previous_take(req)
    first["take_result"]["areas"]["FILLER"]["take"]["filler_count"] = 9
    older["take_result"]["areas"]["FILLER"]["take"]["filler_count"] = 3
    req["previous_takes"] = [first, older]
    c = by_key(run(req)[0])[("filler_count", None)]
    assert (c.previous, c.improved) == (9, True)
