"""judge — 1초 기록 → 1초 판정 결과 (gaze-tracking.md 5-3 · 5-4 · 6-2 · 6-3 · 7 · 14절)."""

from __future__ import annotations

import re

import pytest

from gaze.config import DEFAULT, GazeConfig
from gaze.core import criteria_version, judge

from .judge_helpers import issue_types, rec, run, secs, span, spoken, totals

# ── 결과 하나 · 공통 필드 ──────────────────────────────────────────────────────


def test_a_result_comes_out_even_without_problems():
    r = run(secs(("CAMERA", 30)), 30_000)
    assert (r.evaluator, r.area, r.measurable, r.state, r.issues) == (
        "gaze",
        "GAZE",
        True,
        "AUDIENCE",
        [],
    )
    assert r.metrics.audience_ratio == 1.0
    assert [(i.t_ms, i.values) for i in r.tally] == [
        (29_000, {"camera_ms": 1000, "total_ms": 1000, "measured_ms": 1000})
    ]


def test_criteria_version_is_feature_version_plus_config_hash():
    assert re.fullmatch(r"gaze-1\.3\+[0-9a-f]{12}", criteria_version())
    assert run(secs(("CAMERA", 3)), 3000).criteria_version == criteria_version()
    assert criteria_version(GazeConfig(script_run_ms=4000)) != criteria_version()


def test_same_input_gives_the_same_result():
    records, voiced = secs(("CAMERA", 20), ("BOTTOM", 10)), spoken(0, 30_000)
    assert run(records, 30_000, 25_000, voiced) == run(records, 30_000, 25_000, voiced)


def test_judge_accepts_the_input_model_and_a_dict_alike():
    from gaze.schemas import GazeInput

    raw = {"t_ms": 3000, "since_ms": 2000, "records": secs(("CAMERA", 3))}
    assert judge(GazeInput.model_validate(raw), 3000) == judge(raw, 3000)


# ── 다섯 문제 ─────────────────────────────────────────────────────────────────


def test_doc_example_script_run_of_4_seconds_has_severity_0_57():
    r = run(secs(("CAMERA", 26), ("BOTTOM", 4)), 30_000)
    assert r.state == "SCRIPT"
    [issue] = r.issues
    assert issue.issue_type == "GAZE_ON_SCRIPT"
    assert (issue.severity, issue.persistence_sec, issue.threshold, issue.bad) == (
        0.5714,
        4.0,
        3000,
        10_000,
    )
    assert issue.evidence == {"script_run_ms": 4000, "script_ratio_short": 0.8}
    assert issue.actionable


def test_a_run_reaching_the_threshold_is_a_problem_with_severity_one_half():
    [issue] = run(secs(("CAMERA", 27), ("BOTTOM", 3)), 30_000).issues
    assert (issue.issue_type, issue.severity) == ("GAZE_ON_SCRIPT", 0.5)


def test_severity_stops_at_one_past_the_bad_value():
    [issue] = run(secs(("CAMERA", 15), ("BOTTOM", 15)), 30_000).issues
    assert issue.severity == 1.0


def test_script_run_also_needs_the_short_window_script_ratio():
    # 짧은 창이 10초면 대본 4초 이어짐도 비율 0.4 라 우연한 연속으로 본다
    wide = GazeConfig(short_window_ms=10_000)
    records = secs(("CAMERA", 26), ("BOTTOM", 4))
    assert issue_types(run(records, 30_000, config=wide)) == []
    assert issue_types(run(records, 30_000)) == ["GAZE_ON_SCRIPT"]


def test_screen_run_of_5_seconds_is_on_screen():
    r = run(secs(("CAMERA", 25), ("SCREEN", 5)), 30_000)
    [issue] = r.issues
    assert (issue.issue_type, issue.severity, issue.evidence) == (
        "GAZE_ON_SCREEN",
        0.5,
        {"screen_run_ms": 5000},
    )
    assert r.state == "SCREEN"


def test_away_run_continues_across_directions():
    records = secs(("CAMERA", 27)) + [
        rec(27_000, "OTHER", direction="LEFT"),
        rec(28_000, "OTHER", direction="UP"),
        rec(29_000, "OTHER", direction="RIGHT"),
    ]
    r = run(records, 30_000)
    assert r.metrics.away_run_ms == 3000
    [issue] = r.issues
    assert (issue.issue_type, issue.persistence_sec) == ("GAZE_AWAY", 3.0)
    assert issue.evidence == {"away_run_ms": 3000, "away_ratio_short": 0.6}


def test_low_eye_contact_when_the_audience_share_is_low():
    alternating = [rec(t, "SCREEN" if t // 1000 % 2 else "BOTTOM") for t in range(0, 25_000, 1000)]
    r = run(alternating + secs(("CAMERA", 5), t0=25_000), 30_000)
    [issue] = r.issues
    assert issue.issue_type == "GAZE_LOW_EYE_CONTACT"
    assert r.metrics.audience_ratio == pytest.approx(0.1667)
    assert issue.severity == pytest.approx(0.7222)
    # 마지막 5초는 청중을 봤으므로 '청중을 보지 않음'은 지금 이어지지 않는다
    assert (issue.threshold, issue.bad, issue.persistence_sec) == (0.3, 0.0, 0.0)
    assert issue.evidence == {"audience_ratio": 0.1667, "audience_ratio_short": 1.0}


def test_low_eye_contact_persistence_is_the_run_not_looking_at_the_audience():
    # 청중 5초 → 화면 · 대본을 번갈아 25초: 판정 시각까지 청중을 보지 않은 25초가 이어진다
    alternating = [
        rec(t, "SCREEN" if t // 1000 % 2 else "BOTTOM") for t in range(5_000, 30_000, 1000)
    ]
    r = run(secs(("CAMERA", 5)) + alternating, 30_000)
    issue = next(i for i in r.issues if i.issue_type == "GAZE_LOW_EYE_CONTACT")
    assert issue.persistence_sec == 25.0


def test_low_eye_contact_waits_for_enough_measured_time():
    alternating = [rec(t, "SCREEN" if t // 1000 % 2 else "BOTTOM") for t in range(0, 14_000, 1000)]
    assert issue_types(run(alternating, 14_000)) == []
    longer = [rec(t, "SCREEN" if t // 1000 % 2 else "BOTTOM") for t in range(0, 15_000, 1000)]
    assert issue_types(run(longer, 15_000)) == ["GAZE_LOW_EYE_CONTACT"]


def test_several_problems_can_come_together():
    r = run(secs(("SCREEN", 10), ("BOTTOM", 20)), 30_000)
    assert issue_types(r) == ["GAZE_ON_SCRIPT", "GAZE_LOW_EYE_CONTACT"]


# ── 이어짐이 끊기는 곳 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("breaker", ["UNCERTAIN", "GAP", "CAMERA", "UNMEASURED"])
def test_a_run_breaks_on_another_state_or_a_missing_second(breaker):
    r = run(secs(("CAMERA", 25), ("BOTTOM", 2), (breaker, 1), ("BOTTOM", 2)), 30_000)
    assert r.metrics.script_run_ms == 2000
    assert "GAZE_ON_SCRIPT" not in issue_types(r)


def test_the_run_is_zero_when_the_last_second_is_not_a_look():
    r = run(secs(("BOTTOM", 29), ("UNCERTAIN", 1)), 30_000)
    assert (r.metrics.script_run_ms, r.metrics.screen_run_ms, r.metrics.away_run_ms) == (0, 0, 0)


# ── Take 시작 직후 ────────────────────────────────────────────────────────────


def test_no_gaze_problem_right_after_the_take_starts():
    r = run(secs(("BOTTOM", 4)), 4000)
    assert (r.measurable, r.metrics.script_run_ms, r.issues) == (True, 4000, [])
    assert issue_types(run(secs(("BOTTOM", 5)), 5000)) == ["GAZE_ON_SCRIPT"]


def test_unmeasurable_is_reported_even_right_after_the_start():
    r = run(secs(("UNMEASURED", 3)), 3000)
    assert issue_types(r) == ["GAZE_UNMEASURABLE"]


# ── 측정 불가 ─────────────────────────────────────────────────────────────────


def _unmeasurable(r):
    assert (r.measurable, r.state) == (False, "UNMEASURABLE")
    [issue] = r.issues
    assert (issue.issue_type, issue.actionable, issue.confidence) == (
        "GAZE_UNMEASURABLE",
        False,
        1.0,
    )
    assert (issue.threshold, issue.bad) == (0.5, 0.0)
    return issue


@pytest.mark.parametrize("state", ["UNCERTAIN", "UNMEASURED", "GAP"])
def test_unmeasurable_when_the_short_window_is_not_measured(state):
    issue = _unmeasurable(run(secs(("BOTTOM", 25), (state, 5)), 30_000))
    assert issue.severity == 1.0
    assert issue.persistence_sec == 5.0


def test_unmeasurable_hides_other_problems():
    r = run(secs(("BOTTOM", 27), ("UNCERTAIN", 3)), 30_000)
    _unmeasurable(r)
    assert r.metrics.script_ratio is not None


def test_unmeasurable_when_reliability_is_low():
    records = [rec(t, "CAMERA", reliability=0.3) for t in range(0, 30_000, 1000)]
    r = run(records, 30_000)
    issue = _unmeasurable(r)
    assert issue.severity == 0.7  # 신뢰도 0.3: 0.5 + 0.5 × (0.5 − 0.3) / 0.5
    assert issue.evidence == {"measured_ratio": 1.0, "mean_reliability": 0.3}
    # 모두 측정됐지만 짧은 창 5초 내내 신뢰도가 기준 미만이었다 — 0초가 아니다
    assert issue.persistence_sec == 5.0


def test_unmeasurable_persistence_is_the_run_up_to_now_not_the_sum():
    # 짧은 창 5초: 신뢰도 낮음 2 · 정상 1 · 기록 없음 2 → 지금까지 이어진 것은 기록 없음 2초뿐
    # (architecture.md 7절 '이어진 시간'. 앞의 신뢰도 낮은 2초는 정상 1초에서 끊긴다)
    records = [rec(t, "CAMERA") for t in range(0, 25_000, 1000)]
    records += [rec(25_000, "CAMERA", reliability=0.1), rec(26_000, "CAMERA", reliability=0.1)]
    records += [rec(27_000, "CAMERA")]
    issue = _unmeasurable(run(records, 30_000))
    assert issue.persistence_sec == 2.0


def test_unmeasurable_by_short_reliability_stays_in_the_short_window():
    # 최근 창의 측정 비율이 짧은 창보다 조금 나쁘지만 기준 이상이고, 짧은 창 신뢰도가 원인이다.
    # 이어진 시간은 짧은 창(5초)에서 잰다 — 20초 전의 신뢰도 낮은 1초는 넣지 않는다
    records = [rec(t, "CAMERA") for t in range(0, 25_000, 1000) if t != 5_000]
    records[5] = rec(6_000, "CAMERA", reliability=0.1)
    records += [rec(t, "CAMERA", reliability=0.2) for t in range(25_000, 30_000, 1000)]
    issue = _unmeasurable(run(records, 30_000))
    assert issue.persistence_sec == 5.0


def test_unusable_second_edges_reliability_at_threshold_and_uncertain():
    # 짧은 창: 기준과 같은 0.5(쓸 수 있음) 1 · UNCERTAIN 2 · 기록 없음 2 → 쓸 수 없던 시간 4초
    records = [rec(t, "CAMERA") for t in range(0, 25_000, 1000)]
    records += [rec(25_000, "CAMERA", reliability=0.5)]
    records += [rec(26_000, "UNCERTAIN"), rec(27_000, "UNCERTAIN")]
    issue = _unmeasurable(run(records, 30_000))
    assert issue.persistence_sec == 4.0


def test_measured_ratio_uses_the_worse_of_short_and_recent_windows():
    # 짧은 창은 모두 측정됐지만 최근 30초는 1/3 만 -- 잠깐 좋아진 1초로 다시 열지 않는다
    r = run(secs(("GAP", 20), ("CAMERA", 10)), 30_000)
    # 최근 창에서 재지만 지금 10초째 잘 재고 있으므로 이어진 시간은 0
    assert _unmeasurable(r).persistence_sec == 0.0
    issue = _unmeasurable(r)
    assert r.metrics.measured_ratio == 1.0
    assert issue.evidence["measured_ratio"] == 0.3333


def test_unmeasurable_persistence_stays_inside_the_deciding_window():
    # 정상 10초 → 기록 없음 20초: 짧은 창(측정 0)이 측정 불가를 가르므로, 기록 없음이 20초째
    # 이어져도 그 창 길이 5초까지만 센다(카메라가 끊긴 예시와 같다)
    r = run(secs(("CAMERA", 10)), 30_000)
    assert _unmeasurable(r).persistence_sec == 5.0


def test_doc_example_camera_lost_after_24_seconds():
    r = run([rec(24_000, "CAMERA", reliability=0.95)], 30_000, 29_000)
    issue = _unmeasurable(r)
    m = r.metrics
    assert (m.measured_ratio, m.mean_reliability, m.audience_ratio_short, m.script_run_ms) == (
        0.0,
        0.0,
        None,
        0,
    )
    assert [(i.t_ms, i.values) for i in r.tally] == [
        (29_000, {"unmeasured_ms": 1000, "total_ms": 1000})
    ]
    assert (issue.severity, issue.persistence_sec) == (1.0, 5.0)
    assert issue.evidence == {"measured_ratio": 0.0, "mean_reliability": 0.0}


def test_no_records_at_all_is_unmeasurable():
    r = run([], 30_000, 29_000)
    _unmeasurable(r)
    assert r.metrics.audience_ratio is None
    assert r.metrics.measured_ratio == 0.0


def test_the_take_start_moment_is_unmeasurable():
    r = run([], 0, 0)
    _unmeasurable(r)
    assert r.tally == []


# ── 상태 ──────────────────────────────────────────────────────────────────────


def test_state_is_the_most_looked_place_with_camera_first_on_ties():
    r = run(secs(("CAMERA", 25), ("CAMERA", 2), ("SCREEN", 2), ("OTHER", 1)), 30_000)
    assert r.state == "AUDIENCE"
    r = run(secs(("CAMERA", 25), ("OTHER", 2), ("SCREEN", 2), ("CAMERA", 1)), 30_000)
    assert r.state == "SCREEN"  # SCREEN 2 · OTHER 2 · CAMERA 1 → 동점은 SCREEN 이 앞선다


def test_state_is_script_when_the_short_script_share_reaches_the_threshold():
    # 짧은 창: CAMERA 2 · BOTTOM 2 · UNCERTAIN 1 → 대본 비율 0.5 (동점이면 CAMERA 였을 것)
    r = run(secs(("CAMERA", 25), ("CAMERA", 2), ("BOTTOM", 2), ("UNCERTAIN", 1)), 30_000)
    assert (r.measurable, r.state) == (True, "SCRIPT")


def test_state_does_not_follow_the_last_second():
    r = run(secs(("CAMERA", 29), ("OTHER", 1)), 30_000)
    assert r.state == "AUDIENCE"


# ── 지표 ──────────────────────────────────────────────────────────────────────


def test_ratios_are_over_measured_time_only():
    records = secs(("CAMERA", 10), ("UNCERTAIN", 10), ("BOTTOM", 5), ("SCREEN", 5))
    m = run(records, 30_000).metrics
    assert (m.audience_ratio, m.script_ratio, m.screen_ratio, m.away_ratio) == (
        0.5,
        0.25,
        0.25,
        0.0,
    )
    assert (m.audience_ratio_short, m.script_ratio_short, m.away_ratio_short) == (0.0, 0.0, 0.0)


def test_mean_reliability_is_the_average_over_short_window_records():
    records = secs(("CAMERA", 25)) + [
        rec(t, "CAMERA", reliability=0.6) for t in range(25_000, 30_000, 1000)
    ]
    assert run(records, 30_000).metrics.mean_reliability == 0.6


def test_metrics_have_every_key_with_null_when_nothing_is_measured():
    m = run(secs(("UNCERTAIN", 30)), 30_000).metrics.model_dump()
    assert set(m) == {
        "audience_ratio", "screen_ratio", "script_ratio", "away_ratio", "reading_ratio",
        "audience_ratio_short", "script_ratio_short", "away_ratio_short", "measured_ratio",
        "mean_reliability", "script_run_ms", "screen_run_ms", "away_run_ms",
    }  # fmt: skip
    assert m["audience_ratio"] is None and m["measured_ratio"] == 0.0


# ── 읽기 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "SCRIPT"},
        {"state": None},
        {"confidence": 1.5},
        {"reliability": -0.1},
        {"frames": -1},
        {"duration_ms": 0},
    ],
)
def test_an_unreadable_record_drops_only_its_second(bad):
    records = secs(("CAMERA", 30))
    records[29] = {**records[29], **bad}
    r = run(records, 30_000)
    assert r.measurable
    assert [i.values for i in r.tally] == [{"unmeasured_ms": 1000, "total_ms": 1000}]


@pytest.mark.parametrize("item", ["CAMERA", None, 3, ["CAMERA"]])
def test_a_record_that_is_not_an_object_is_dropped(item):
    r = run(secs(("CAMERA", 29)) + [item], 30_000)
    assert r.tally[0].values == {"unmeasured_ms": 1000, "total_ms": 1000}


def test_a_negative_record_time_is_dropped():
    r = run(secs(("CAMERA", 30)) + [rec(-1000, "BOTTOM")], 30_000, 0)
    assert totals(r)["camera_ms"] == 30_000


def test_the_first_of_two_records_for_the_same_second_wins():
    records = secs(("CAMERA", 29)) + [rec(29_000, "BOTTOM"), rec(29_000, "CAMERA")]
    assert run(records, 30_000).tally[0].values["script_ms"] == 1000


def test_a_record_overlapping_the_previous_one_is_dropped():
    records = secs(("CAMERA", 29)) + [rec(29_000, "BOTTOM"), rec(29_500, "OTHER")]
    r = run(records, 30_000)
    assert [(i.t_ms, i.values) for i in r.tally] == [
        (29_000, {"script_ms": 1000, "total_ms": 1000, "measured_ms": 1000})
    ]


def test_records_arriving_out_of_order_are_sorted():
    assert (
        run(list(reversed(secs(("CAMERA", 26), ("BOTTOM", 4)))), 30_000).metrics.script_run_ms
        == 4000
    )


def test_a_record_after_the_judgment_time_is_ignored():
    r = run(secs(("CAMERA", 30)) + [rec(30_000, "BOTTOM")], 30_000)
    assert (r.metrics.script_run_ms, totals(r).get("script_ms", 0)) == (0, 0)


def test_unknown_record_fields_and_condition_issues_are_ignored():
    records = [rec(t, "CAMERA", issues=["NEW_ISSUE"], extra=1) for t in range(0, 30_000, 1000)]
    assert run(records, 30_000).measurable


def test_the_last_short_piece_counts_its_real_length():
    records = secs(("CAMERA", 29)) + [rec(29_000, "BOTTOM", duration_ms=400)]
    r = run(records, 29_400, 29_000)
    assert r.tally[0].values == {"script_ms": 400, "total_ms": 400, "measured_ms": 400}


# ── 커서 · 새 몫 ──────────────────────────────────────────────────────────────


def test_only_seconds_from_the_cursor_are_new():
    r = run(secs(("CAMERA", 30)), 30_000, 27_000)
    assert [i.t_ms for i in r.tally] == [27_000, 28_000, 29_000]
    assert r.counted_until_ms == 30_000


def test_missed_requests_are_counted_at_once():
    r = run(secs(("CAMERA", 20), ("BOTTOM", 10)), 30_000, 20_000)
    assert totals(r) == {"script_ms": 10_000, "total_ms": 10_000, "measured_ms": 10_000}


def test_calling_again_with_the_returned_cursor_counts_nothing_twice():
    records = secs(("CAMERA", 30))
    first = run(records, 30_000, 25_000)
    again = run(records, 30_000, first.counted_until_ms)
    assert (len(first.tally), again.tally) == (5, [])


def test_the_cursor_never_goes_back():
    r = run(secs(("CAMERA", 30)), 30_000, 31_000)
    assert (r.counted_until_ms, r.tally) == (31_000, [])


def test_missing_time_is_counted_one_second_at_a_time():
    r = run(secs(("CAMERA", 26)), 30_000, 25_000)
    assert [(i.t_ms, i.values) for i in r.tally] == [
        (25_000, {"camera_ms": 1000, "total_ms": 1000, "measured_ms": 1000}),
        (26_000, {"unmeasured_ms": 1000, "total_ms": 1000}),
        (27_000, {"unmeasured_ms": 1000, "total_ms": 1000}),
        (28_000, {"unmeasured_ms": 1000, "total_ms": 1000}),
        (29_000, {"unmeasured_ms": 1000, "total_ms": 1000}),
    ]


def test_tally_covers_exactly_the_new_time():
    records = secs(("CAMERA", 5), ("GAP", 3), ("UNCERTAIN", 2), ("BOTTOM", 10), ("GAP", 10))
    r = run(records, 30_000, 2_000, spoken(0, 30_000))
    t = totals(r)
    assert t["total_ms"] == 28_000
    assert t["measured_ms"] == t["camera_ms"] + t["script_ms"] == 13_000
    assert (t["uncertain_ms"], t["unmeasured_ms"]) == (2000, 13_000)


def test_a_cursor_inside_a_missing_stretch_counts_from_the_cursor():
    r = run(secs(("CAMERA", 20)), 30_000, 25_500)
    assert r.tally[0].t_ms == 25_500 and r.tally[0].values["unmeasured_ms"] == 500
    assert totals(r)["total_ms"] == 4_500


# ── 대본 의존 ─────────────────────────────────────────────────────────────────


def test_speaking_and_reading_are_counted_per_spoken_second():
    records = secs(("CAMERA", 2), ("BOTTOM", 2), ("GAP", 1))
    r = run(records, 5000, 0, spoken(1000, 5000))
    assert totals(r) == {
        "camera_ms": 2000, "script_ms": 2000, "unmeasured_ms": 1000, "measured_ms": 4000,
        "total_ms": 5000, "speaking_ms": 4000, "reading_ms": 2000,
    }  # fmt: skip


def test_a_second_below_the_voiced_threshold_is_not_spoken():
    r = run(secs(("BOTTOM", 3)), 3000, 0, spoken(0, 3000, voiced_ms=299))
    assert "speaking_ms" not in totals(r)
    r = run(secs(("BOTTOM", 3)), 3000, 0, spoken(0, 3000, voiced_ms=300))
    assert totals(r)["reading_ms"] == 3000


def test_reading_ratio_is_script_share_of_spoken_time():
    records = secs(("CAMERA", 20), ("BOTTOM", 10))
    voiced = spoken(10_000, 30_000)  # 말한 20초 중 대본 10초
    assert run(records, 30_000, voiced=voiced).metrics.reading_ratio == 0.5


def test_without_voice_records_reading_is_null_and_runs_zero():
    r = run(secs(("CAMERA", 20), ("BOTTOM", 5), ("CAMERA", 5)), 30_000, 0)
    assert r.metrics.reading_ratio is None
    assert "reading_runs" not in totals(r) and "speaking_ms" not in totals(r)
    assert issue_types(r) == []  # 나머지 판정은 그대로


def test_reading_run_counts_when_spoken_script_run_ends():
    records = secs(("CAMERA", 2), ("BOTTOM", 3), ("CAMERA", 1))
    r = run(records, 6000, 5000, spoken(0, 6000))
    assert [(i.t_ms, i.values.get("reading_runs")) for i in r.tally] == [(5000, 1)]


def test_reading_run_shorter_than_the_threshold_is_not_counted():
    records = secs(("CAMERA", 2), ("BOTTOM", 2), ("CAMERA", 2))
    assert "reading_runs" not in totals(run(records, 6000, 0, spoken(0, 6000)))


def test_reading_run_still_going_at_the_judgment_time_is_not_counted():
    records = secs(("CAMERA", 2), ("BOTTOM", 5))
    assert "reading_runs" not in totals(run(records, 7000, 0, spoken(0, 7000)))


@pytest.mark.parametrize("breaker", ["UNCERTAIN", "GAP", "SCREEN"])
def test_reading_run_breaks_on_another_state_or_a_missing_second(breaker):
    records = secs(("BOTTOM", 2), (breaker, 1), ("BOTTOM", 2), ("CAMERA", 1))
    assert "reading_runs" not in totals(run(records, 6000, 0, spoken(0, 6000)))


def test_reading_run_breaks_on_a_silent_second():
    records = secs(("BOTTOM", 5), ("CAMERA", 1))
    voiced = spoken(0, 2000) + spoken(3000, 6000)
    assert "reading_runs" not in totals(run(records, 6000, 0, voiced))


def test_reading_run_is_counted_once_across_requests():
    records, voiced = secs(("BOTTOM", 4), ("CAMERA", 2)), spoken(0, 6000)
    counted = [
        totals(run(records[: t // 1000], t, t - 1000, voiced)).get("reading_runs", 0)
        for t in range(1000, 7000, 1000)
    ]
    assert counted == [0, 0, 0, 0, 1, 0]


def test_silent_script_looks_are_not_reading():
    r = run(secs(("BOTTOM", 10)), 10_000, 0, spoken(0, 10_000, voiced_ms=0))
    t = totals(r)
    assert (t["script_ms"], t.get("reading_ms", 0), r.metrics.reading_ratio) == (10_000, 0, None)


def test_the_first_of_two_voice_records_for_the_same_second_wins():
    voiced = [{"t_ms": 0, "voiced_ms": 0}, {"t_ms": 0, "voiced_ms": 900}]
    assert "speaking_ms" not in totals(run(secs(("BOTTOM", 1)), 1000, 0, voiced))


def test_judge_does_not_change_its_inputs():
    raw = {
        "t_ms": 30_000,
        "since_ms": 25_000,
        "records": secs(("CAMERA", 30)),
        "voiced": spoken(0, 30_000),
    }
    snapshot = repr(raw)
    judge(raw, 30_000, DEFAULT)
    assert repr(raw) == snapshot


# ── 경계 (검증에서 더함) ───────────────────────────────────────────────────────


def test_metrics_use_only_the_overlap_with_each_window():
    records = (
        span("BOTTOM", 0, 1000)
        + span("CAMERA", 1000, 25_500)
        + span("BOTTOM", 25_500, 26_500)
        + span("CAMERA", 26_500, 31_000)
    )
    m = run(records, 31_000).metrics
    assert (m.script_ratio, m.audience_ratio) == (0.0333, 0.9667)
    assert (m.script_ratio_short, m.audience_ratio_short) == (0.1, 0.9)


@pytest.mark.parametrize(
    ("reliability", "measurable"), [(0.4999, False), (0.5, True), (0.5001, True)]
)
def test_measurement_is_available_at_and_above_the_reliability_minimum(reliability, measurable):
    r = run(span("CAMERA", 0, 30_000, reliability=reliability), 30_000)
    assert r.measurable is measurable
    assert issue_types(r) == ([] if measurable else ["GAZE_UNMEASURABLE"])


@pytest.mark.parametrize(("measured_ms", "measurable"), [(2499, False), (2500, True), (2501, True)])
def test_measurement_is_available_at_and_above_the_measured_minimum(measured_ms, measurable):
    records = span("CAMERA", 0, 25_000) + span("CAMERA", 30_000 - measured_ms, 30_000)
    r = run(records, 30_000)
    assert r.measurable is measurable
    assert issue_types(r) == ([] if measurable else ["GAZE_UNMEASURABLE"])


@pytest.mark.parametrize(
    ("state", "issue", "length", "expected"),
    [
        ("SCREEN", "GAZE_ON_SCREEN", 4999, False),
        ("SCREEN", "GAZE_ON_SCREEN", 5000, True),
        ("SCREEN", "GAZE_ON_SCREEN", 5001, True),
        ("OTHER", "GAZE_AWAY", 1999, False),
        ("OTHER", "GAZE_AWAY", 2000, True),
        ("OTHER", "GAZE_AWAY", 2001, True),
    ],
)
def test_each_run_issue_obeys_its_threshold_boundary(state, issue, length, expected):
    records = span("CAMERA", 0, 30_000 - length) + span(state, 30_000 - length, 30_000)
    r = run(records, 30_000)
    assert (issue in issue_types(r)) is expected
    if length in (2000, 5000):
        assert r.issues[0].severity == 0.5


@pytest.mark.parametrize(("camera_s", "expected"), [(9, False), (8, True)])
def test_low_eye_contact_starts_below_the_threshold(camera_s, expected):
    # 청중 9/30 = 0.3 은 기준 미만이 아니다
    other = 30 - camera_s
    records = [rec(t, "SCREEN" if t // 1000 % 2 else "OTHER") for t in range(0, other * 1000, 1000)]
    r = run(records + secs(("CAMERA", camera_s), t0=other * 1000), 30_000)
    assert ("GAZE_LOW_EYE_CONTACT" in issue_types(r)) is expected


@pytest.mark.parametrize(("length", "runs"), [(2999, 0), (3000, 1), (3001, 1)])
def test_reading_run_threshold_boundary(length, runs):
    records = span("BOTTOM", 0, length) + [rec(length, "UNCERTAIN"), rec(length + 1000, "CAMERA")]
    r = run(records, length + 2000, 0, spoken(0, length))
    assert totals(r).get("reading_runs", 0) == runs


@pytest.mark.parametrize("breaker", ["UNCERTAIN", "UNMEASURED", "GAP", "SCREEN", "silent"])
def test_a_qualified_reading_run_is_counted_when_any_breaker_ends_it(breaker):
    state = "BOTTOM" if breaker == "silent" else breaker
    records = secs(("BOTTOM", 3), (state, 1), ("CAMERA", 1))
    voiced = spoken(0, 3000) if breaker == "silent" else spoken(0, 5000)
    first = run(records, 5000, 0, voiced)
    assert [
        (i.t_ms, i.values["reading_runs"]) for i in first.tally if "reading_runs" in i.values
    ] == [(3000, 1)]
    assert "reading_runs" not in totals(run(records, 5000, first.counted_until_ms, voiced))


def test_a_record_starting_before_the_cursor_is_not_counted_again():
    r = run(secs(("CAMERA", 30)), 30_000, 29_500)
    assert (r.tally, r.counted_until_ms) == ([], 30_000)


def test_every_ratio_is_null_when_its_denominator_is_zero():
    m = run(secs(("UNCERTAIN", 30)), 30_000).metrics
    ratios = (
        m.audience_ratio, m.screen_ratio, m.script_ratio, m.away_ratio, m.reading_ratio,
        m.audience_ratio_short, m.script_ratio_short, m.away_ratio_short,
    )  # fmt: skip
    assert ratios == (None,) * 8
    assert (m.measured_ratio, m.script_run_ms, m.screen_run_ms, m.away_run_ms) == (0.0, 0, 0, 0)


def test_missing_voice_records_preserve_gaze_problems():
    r = run(secs(("CAMERA", 26), ("BOTTOM", 4)), 30_000, 0)
    assert r.state == "SCRIPT"
    [issue] = r.issues
    assert (issue.issue_type, issue.severity) == ("GAZE_ON_SCRIPT", 0.5714)
    assert r.metrics.reading_ratio is None
    assert not {"speaking_ms", "reading_ms", "reading_runs"} & set(totals(r))


def test_issue_confidence_uses_the_measurement_ratio_of_its_window():
    alternating = [
        rec(t, "SCREEN" if t // 1000 % 2 else "BOTTOM") for t in range(3000, 14_000, 1000)
    ]
    records = secs(("CAMERA", 3)) + alternating + secs(("BOTTOM", 4), t0=26_000)
    r = run(records, 30_000)
    assert {i.issue_type: i.confidence for i in r.issues} == {
        "GAZE_ON_SCRIPT": 0.8,
        "GAZE_LOW_EYE_CONTACT": 0.6,
    }


def test_records_with_null_optional_fields_are_read_as_zero():
    # 코치의 요청 모델은 빠진 confidence · frames 를 null 로 넘긴다
    records = [rec(t, "CAMERA", confidence=None, frames=None) for t in range(0, 30_000, 1000)]
    r = run(records, 30_000)
    assert (r.measurable, totals(r)) == (
        True,
        {"camera_ms": 1000, "total_ms": 1000, "measured_ms": 1000},
    )


def test_a_record_with_a_non_string_direction_keeps_its_second():
    records = secs(("CAMERA", 29)) + [rec(29_000, "OTHER", direction=[])]
    assert run(records, 30_000).tally[0].values["away_ms"] == 1000


def test_mean_reliability_averages_records_not_milliseconds():
    records = span("CAMERA", 0, 4000, reliability=0.4) + [
        rec(4000, "CAMERA", duration_ms=500, reliability=1.0)
    ]
    r = run(records, 4500)
    assert (r.metrics.mean_reliability, r.measurable) == (0.52, True)


def test_a_huge_time_jump_does_not_make_a_huge_tally():
    r = run([], 10**9, 0)
    assert len(r.tally) <= 31
    assert totals(r) == {"unmeasured_ms": 10**9, "total_ms": 10**9}


def test_spoken_time_is_the_overlap_with_spoken_seconds():
    r = run([], 1500, 0, [{"t_ms": 500, "voiced_ms": 800}])
    assert totals(r)["speaking_ms"] == 1000


@pytest.mark.parametrize(("away_s", "severity"), [(2, 0.5), (5, 0.75), (8, 1.0), (10, 1.0)])
def test_run_severity_is_one_half_at_threshold_and_one_at_bad(away_s, severity):
    [issue] = run(secs(("CAMERA", 30 - away_s), ("OTHER", away_s)), 30_000).issues
    assert (issue.issue_type, issue.severity) == ("GAZE_AWAY", severity)
