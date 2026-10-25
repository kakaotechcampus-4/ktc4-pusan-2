"""지표 · 상태 · 문제 — 최근 창, 분당 수, 잦음(HIGH), STT 끊김 (filler.md 5-4, 6-1 6, 7, 14절)."""

from __future__ import annotations

import pytest

from filler.config import FillerConfig

from .helpers import PLAIN, fillers, run, time_items, total, word

# ── 최근 창 ────────────────────────────────────────────────────────────────────


def test_no_words_gives_zero_fillers_and_normal_state():
    result = run(30_000)
    assert (result.state, result.measurable) == ("NORMAL", True)
    assert result.metrics.recent_filler_count == 0
    assert result.metrics.filler_per_min == 0.0
    assert result.issues == []


def test_recent_count_includes_words_that_started_exactly_at_the_window_start():
    result = run(70_000, [word("음", 10_000, 10_300)])
    assert result.metrics.recent_filler_count == 1


def test_recent_count_excludes_words_that_started_before_the_window():
    result = run(70_000, [word("음", 9_999, 10_300)])
    assert result.metrics.recent_filler_count == 0


def test_recent_count_includes_words_that_end_exactly_at_t():
    assert run(70_000, [word("음", 69_700, 70_000)]).metrics.recent_filler_count == 1


def test_recent_count_excludes_words_that_end_after_t():
    assert run(70_000, [word("음", 69_800, 70_001)]).metrics.recent_filler_count == 0


def test_recent_count_also_counts_fillers_that_were_already_counted_before():
    result = run(30_000, fillers(3), words_since_ms=10_000)
    assert result.metrics.recent_filler_count == 3
    assert total(result.tally, "filler_count") == 0


def test_a_pending_word_is_not_in_the_recent_count():
    result = run(30_000, [word(PLAIN, 1000, 1500), word("그", 2500, 2600)])
    assert result.words[-1].is_filler is None
    assert result.metrics.recent_filler_count == 0


def test_non_fillers_are_not_in_the_recent_count():
    assert run(30_000, [word(PLAIN, 1000, 1500)]).metrics.recent_filler_count == 0


# ── filler_per_min ─────────────────────────────────────────────────────────────


def test_per_min_at_41_seconds_with_two_fillers_is_2_93():
    # 창(60초)보다 Take 가 짧으면 Take 길이로 나눈다 (filler.md 5-5)
    words = [word("음", 10_000, 10_300), word("음", 30_000, 30_300)]
    assert run(41_000, words).metrics.filler_per_min == 2.93


def test_per_min_divides_by_the_window_when_the_take_is_longer():
    # 100초째, 창은 40~100초(60초)이므로 군더더기 3개 → 3분당 아니라 분당 3
    words = [word("음", 50_000 + k * 1000, 50_300 + k * 1000) for k in range(3)]
    assert run(100_000, words).metrics.filler_per_min == 3.0


def test_per_min_divides_only_by_the_time_after_stt_ok_since():
    words = [word("음", 25_000, 25_300), word("음", 30_000, 30_300)]
    # 관측 시간은 41000 − 20000 = 21000 ms
    assert run(41_000, words, stt_ok_since_ms=20_000).metrics.filler_per_min == 5.71


@pytest.mark.parametrize(("t_ms", "expected"), [(9_999, None), (10_000, 0.0), (10_001, 0.0)])
def test_per_min_is_null_when_the_observed_time_is_under_ten_seconds(t_ms, expected):
    assert run(t_ms).metrics.filler_per_min == expected


def test_per_min_is_null_when_stt_came_back_less_than_ten_seconds_ago():
    result = run(41_000, [word("음", 38_000, 38_300)], stt_ok_since_ms=35_000)
    assert result.metrics.recent_filler_count == 1
    assert result.metrics.filler_per_min is None


# ── 잦음 ──────────────────────────────────────────────────────────────────────


def test_below_the_threshold_is_normal_without_issues():
    result = run(30_000, fillers(5))
    assert (result.state, result.issues) == ("NORMAL", [])


def test_at_the_threshold_is_high_with_one_issue_at_severity_half():
    result = run(30_000, fillers(6))
    assert result.state == "HIGH"
    [issue] = result.issues
    assert (issue.issue_type, issue.area, issue.severity) == ("FILLER_FREQUENT", "FILLER", 0.5)


def test_severity_grows_linearly_between_the_threshold_and_the_bad_value():
    result = run(30_000, fillers(10))
    assert result.issues[0].severity == pytest.approx(0.5 + 0.5 * 4 / 9)


def test_severity_is_one_at_the_bad_value_and_above():
    assert run(30_000, fillers(15)).issues[0].severity == 1.0
    assert run(30_000, fillers(20)).issues[0].severity == 1.0


def test_severity_is_one_when_the_bad_value_equals_the_threshold():
    cfg = FillerConfig(frequent_count=6, frequent_bad=6)
    assert run(30_000, fillers(6), config=cfg).issues[0].severity == 1.0


def test_issue_carries_the_thresholds_confidence_and_evidence():
    result = run(30_000, fillers(7))
    issue = result.issues[0]
    assert (issue.threshold, issue.bad, issue.confidence, issue.actionable) == (
        6.0,
        15.0,
        1.0,
        True,
    )
    assert issue.evidence == {"recent_filler_count": 7, "filler_per_min": 14.0}
    assert result.metrics.filler_per_min == 14.0


def test_issue_evidence_per_min_is_null_when_the_observed_time_is_short():
    cfg = FillerConfig(min_rate_ms=60_000)
    issue = run(30_000, fillers(6), config=cfg).issues[0]
    assert issue.evidence == {"recent_filler_count": 6, "filler_per_min": None}


def test_persistence_counts_from_the_end_of_the_threshold_th_earliest_filler():
    # 7개의 끝: 1300 … 7300. 이른 순 6번째(조건이 성립한 시각)는 6300 이므로 (8000 − 6300) ÷ 1000
    result = run(8_000, fillers(7))
    assert result.issues[0].persistence_sec == pytest.approx(1.7)


def test_persistence_is_measured_from_the_sixth_filler_when_exactly_at_the_threshold():
    # 6개의 끝: 1300 … 6300. 6번째가 끝난 6300 부터 7000 까지
    assert run(7_000, fillers(6)).issues[0].persistence_sec == pytest.approx(0.7)


def test_persistence_does_not_change_when_more_fillers_arrive_after_the_threshold_was_reached():
    # 조건은 6번째 군더더기가 끝난 6300 에 성립했고, 그 뒤 군더더기는 시각을 바꾸지 않는다
    assert run(9_000, fillers(7)).issues[0].persistence_sec == pytest.approx(2.7)
    assert run(9_000, fillers(8)).issues[0].persistence_sec == pytest.approx(2.7)


def test_persistence_grows_when_one_more_filler_is_added_before_the_threshold_was_reached():
    # 같은 t_ms 에서 군더더기 하나가 더 이르게 있으면 조건이 더 일찍 성립한 것으로 본다
    base = fillers(6, start_ms=2_000)  # 끝: 2300 … 7300 → 6번째 7300
    earlier = [*fillers(1, start_ms=500), *base]  # 끝: 800, 2300 … 7300 → 6번째 6300
    assert run(9_000, base).issues[0].persistence_sec == pytest.approx(1.7)
    assert run(9_000, earlier).issues[0].persistence_sec == pytest.approx(2.7)


def test_fillers_outside_the_recent_window_do_not_make_it_high():
    result = run(100_000, fillers(7, start_ms=1_000))
    assert (result.state, result.metrics.recent_filler_count) == ("NORMAL", 0)


# ── STT 끊김 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["connecting", "reconnecting", "degraded", "closed"])
def test_stt_not_ok_gives_unknown_with_nothing_measured(status):
    result = run(30_000, fillers(8), stt_status=status, words_since_ms=500, since_ms=29_000)
    assert (result.measurable, result.state, result.issues) == (False, "UNKNOWN", [])
    assert result.words == []
    assert result.metrics.recent_filler_count is None
    assert result.metrics.filler_per_min is None
    assert result.words_counted_until_ms == 500


def test_stt_not_ok_counts_only_total_time():
    result = run(3_500, stt_status="reconnecting", since_ms=1_000)
    assert [(i.t_ms, i.values) for i in time_items(result)] == [
        (1_000, {"total_ms": 1_000}),
        (2_000, {"total_ms": 1_000}),
        (3_000, {"total_ms": 500}),
    ]
    assert result.tally == time_items(result)


def test_missing_stt_status_is_treated_as_not_ok():
    result = run(30_000, fillers(3), stt_status=None)
    assert (result.measurable, result.state) == (False, "UNKNOWN")


def test_stt_back_to_ok_counts_elapsed_time_from_stt_ok_since():
    result = run(5_000, since_ms=3_000, stt_ok_since_ms=4_000)
    assert [i.values for i in time_items(result)] == [
        {"total_ms": 1_000},
        {"elapsed_ms": 1_000, "total_ms": 1_000},
    ]
