"""서버 코어(gaze.core): 1초 기록 → 타임라인 · 코치 이슈 · 테이크 요약 · 비교 · 개입 효과.

* 1초 기록은 필드 8개짜리 dict 로 오가고 그대로 되돌아온다;
* 비율은 측정된 시간으로만 나누고, 연속 구간은 같은 상태끼리 합친다 (OTHER 는 방향이 달라도);
* 이슈는 에이전트 공통 평가기 형식이고 evidence.yaml 의 임계값을 쓴다. GAZE_UNMEASURABLE 은 행동하지 않는다;
* 테이크 요약 · 이전 테이크 비교 · 개입 효과의 모양.

원본: archive tests/test_gaze_evidence.py 의 타임라인 · 이슈 · 리뷰 테스트 (본문 그대로, import 만 코어로)
"""

from __future__ import annotations

import dataclasses

import pytest

from gaze.config import EvidenceConfig
from gaze.core import (
    GAZE_COACH_ACTION,
    GazeSample,
    GazeTimeline,
    compare_summaries,
    evaluate_gaze,
    intervention_outcome,
    take_summary,
)

CFG = EvidenceConfig()


# ==========================================================================
# Wire record
# ==========================================================================


def test_samples_round_trip_through_the_wire_dict():
    s = GazeSample(1000, 1000, "OTHER", "DOWN_LEFT", 0.875, 0.7, ("TOO_FAR",), 8)
    d = s.to_dict()
    assert set(d) == {
        "t_ms",
        "duration_ms",
        "state",
        "direction",
        "confidence",
        "reliability",
        "issues",
        "frames",
    }
    assert GazeSample.from_dict(d) == s


# ==========================================================================
# Timeline
# ==========================================================================


def seconds(*spec, reliability=1.0):
    """("CAMERA", 3), ("OTHER:LEFT", 2) ... -> contiguous 1 s samples from t=0."""
    out, t = [], 0
    for state, n in spec:
        state, _, direction = state.partition(":")
        for _ in range(n):
            out.append(GazeSample(t, 1000, state, direction or None, 1.0, reliability))
            t += 1000
    return GazeTimeline(out)


def test_ratios_divide_by_measured_time_only():
    tl = seconds(("CAMERA", 2), ("UNMEASURED", 2), ("BOTTOM", 1), ("UNCERTAIN", 1))
    st = tl.stats(6000, 6000)
    assert (st["tracked_ms"], st["measured_ms"], st["unmeasured_ms"], st["uncertain_ms"]) == (
        6000,
        3000,
        2000,
        1000,
    )
    assert st["ratio"]["CAMERA"] == pytest.approx(2 / 3)
    assert st["coverage"] == pytest.approx(0.5)


def test_a_window_clips_partial_samples():
    tl = seconds(("CAMERA", 4), ("BOTTOM", 2))
    st = tl.stats(5500, 2000)
    assert st["state_ms"]["CAMERA"] == 500 and st["state_ms"]["BOTTOM"] == 1500


def test_runs_merge_other_across_directions_and_break_on_gaps():
    tl = seconds(("CAMERA", 2), ("OTHER:LEFT", 2), ("OTHER:UP", 1))
    runs = tl.runs()
    assert [(r.state, r.duration_ms, r.direction) for r in runs] == [
        ("CAMERA", 2000, None),
        ("OTHER", 3000, "LEFT"),
    ]
    gappy = GazeTimeline([GazeSample(0, 1000, "CAMERA"), GazeSample(2000, 1000, "CAMERA")])
    assert len(gappy.runs()) == 2


# ==========================================================================
# Coach: issues
# ==========================================================================


def test_reading_the_script_becomes_an_issue_after_three_seconds():
    assert evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 2)), 7000, CFG) == []
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 4)), 9000, CFG)
    assert issue["issue_type"] == "GAZE_ON_SCRIPT"
    assert set(issue) == {
        "evaluator",
        "issue_type",
        "t_ms",
        "severity",
        "confidence",
        "persistence_sec",
        "evidence",
        "actionable",
    }
    assert issue["evaluator"] == "gaze" and issue["actionable"] is True
    assert issue["severity"] == pytest.approx(4000 / CFG.script_full_ms)
    assert issue["persistence_sec"] == 4.0
    assert issue["evidence"]["bottom_ratio_5s"] == pytest.approx(0.8)
    assert GAZE_COACH_ACTION[issue["issue_type"]] == "LOOK_AT_CAMERA"


def test_looking_away_carries_its_direction():
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("OTHER:DOWN_LEFT", 3)), 8000, CFG)
    assert issue["issue_type"] == "GAZE_AWAY"
    assert issue["evidence"]["direction"] == "DOWN_LEFT"


def test_low_eye_contact_over_the_long_window():
    tl = seconds(*[("SCREEN", 4), ("CAMERA", 1)] * 4)
    issues = {i["issue_type"]: i for i in evaluate_gaze(tl, 20000, CFG)}
    low = issues["GAZE_LOW_EYE_CONTACT"]
    assert low["evidence"]["camera_ratio_30s"] == pytest.approx(0.2)
    assert low["severity"] == pytest.approx((0.3 - 0.2) / 0.3, abs=1e-4)


def test_an_unusable_gaze_is_reported_but_not_actionable():
    issues = evaluate_gaze(seconds(("CAMERA", 2), ("UNMEASURED", 4)), 6000, CFG)
    (unmeasurable,) = [i for i in issues if i["issue_type"] == "GAZE_UNMEASURABLE"]
    assert unmeasurable["actionable"] is False
    assert unmeasurable["evidence"]["coverage_5s"] == pytest.approx(0.2)


def test_low_reliability_alone_makes_the_gaze_unusable():
    tl = seconds(("CAMERA", 6), reliability=0.3)
    assert any(i["issue_type"] == "GAZE_UNMEASURABLE" for i in evaluate_gaze(tl, 6000, CFG))


def test_issues_are_most_severe_first_and_evaluated_as_of_t():
    tl = seconds(("SCREEN", 25), ("BOTTOM", 10))
    issues = evaluate_gaze(tl, 35000, CFG)
    assert [i["severity"] for i in issues] == sorted((i["severity"] for i in issues), reverse=True)
    early = evaluate_gaze(tl, 20000, CFG)  # replay: as it stood at 20 s
    assert early[0]["issue_type"] == "GAZE_ON_SCREEN"


# ==========================================================================
# Review: summary, delta, intervention outcome
# ==========================================================================


def test_take_summary_has_statistics_segments_and_problem_segments():
    tl = seconds(("CAMERA", 5), ("BOTTOM", 4), ("CAMERA", 3), ("OTHER:LEFT", 3), ("UNMEASURED", 1))
    s = take_summary(tl, CFG)
    assert s["eye_contact_ratio"] == pytest.approx(8 / 15, abs=1e-4)  # reported to 4 decimals
    assert s["other_direction_ms"]["LEFT"] == 3000
    assert [p["issue_type"] for p in s["problem_segments"]] == ["GAZE_ON_SCRIPT", "GAZE_AWAY"]
    assert s["problem_segments"][1]["direction"] == "LEFT"
    assert s["longest_run_ms"]["CAMERA"] == 5000 and s["episodes"]["CAMERA"] == 2
    assert len(s["segments"]) == 5


def test_previous_take_delta():
    prev = take_summary(seconds(("CAMERA", 2), ("BOTTOM", 8)), CFG)
    cur = take_summary(seconds(("CAMERA", 6), ("BOTTOM", 4)), CFG)
    delta = compare_summaries(prev, cur)
    assert delta["eye_contact_ratio"] == {"previous": 0.2, "current": 0.6, "delta": 0.4}
    assert delta["state_ratio"]["BOTTOM"]["delta"] == pytest.approx(-0.4)


def test_intervention_outcome_in_the_coach_history_shape():
    tl = seconds(("BOTTOM", 10), ("CAMERA", 10))  # feedback at 10 s, presenter looks up
    out = intervention_outcome(tl, 10000, "GAZE_ON_SCRIPT", CFG)
    assert out["intervention"] == {
        "type": "LOOK_AT_CAMERA",
        "issue_type": "GAZE_ON_SCRIPT",
        "t_ms": 10000,
    }
    assert out["before"]["bottom_ratio_5s"] == 1.0
    assert out["after_5s"]["bottom_ratio_5s"] == 0.0
    assert out["effective"] is True
    pending = intervention_outcome(seconds(("BOTTOM", 10)), 10000, "GAZE_ON_SCRIPT", CFG)
    assert pending["effective"] is None
    with pytest.raises(ValueError):
        intervention_outcome(tl, 0, "GAZE_UNMEASURABLE", CFG)


def test_config_windows_name_the_evidence_keys():
    cfg = dataclasses.replace(CFG, short_window_ms=2500)
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 4)), 9000, cfg)
    assert "bottom_ratio_2.5s" in issue["evidence"]
