"""summarize · criteria (gaze-tracking.md 5-3 · 6-4 · 14절, architecture.md 7절)."""

from __future__ import annotations

import pytest

from gaze.config import DEFAULT, GazeConfig
from gaze.core import criteria, summarize

from .judge_helpers import run, secs, spoken, totals


def test_summarize_divides_by_measured_and_spoken_time():
    g = summarize({"GAZE": {"camera_ms": 6000, "screen_ms": 2000, "script_ms": 1000,
                            "away_ms": 1000, "measured_ms": 10_000, "total_ms": 20_000,
                            "speaking_ms": 4000, "reading_ms": 1000, "reading_runs": 2}})["GAZE"]  # fmt: skip
    assert g == {
        "audience_ratio": 0.6, "script_ratio": 0.1, "screen_ratio": 0.2, "away_ratio": 0.1,
        "reading_ratio": 0.25, "reading_runs": 2, "measured_ratio": 0.5,
    }  # fmt: skip


def test_summarize_gives_null_for_zero_denominators_except_measured_ratio():
    g = summarize({"GAZE": {"unmeasured_ms": 5000, "total_ms": 5000}})["GAZE"]
    assert g == {
        "audience_ratio": None, "script_ratio": None, "screen_ratio": None, "away_ratio": None,
        "reading_ratio": None, "reading_runs": 0, "measured_ratio": 0.0,
    }  # fmt: skip


def test_summarize_reads_a_missing_area_as_empty():
    assert summarize({})["GAZE"]["measured_ratio"] == 0.0
    assert summarize({"VOLUME": {"voiced_ms": 1}})["GAZE"]["reading_runs"] == 0


def test_summarize_of_added_tally_matches_the_judgment_ratios():
    # 코치가 tally 를 더해 summarize 하면 같은 기간을 판정한 비율과 같다
    records, voiced = (
        secs(("CAMERA", 12), ("BOTTOM", 6), ("UNCERTAIN", 2), ("SCREEN", 10)),
        spoken(0, 30_000),
    )
    r = run(records, 30_000, 0, voiced)
    g = summarize({"GAZE": totals(r)})["GAZE"]
    m = r.metrics
    assert (g["audience_ratio"], g["script_ratio"], g["screen_ratio"], g["reading_ratio"]) == (
        m.audience_ratio,
        m.script_ratio,
        m.screen_ratio,
        m.reading_ratio,
    )


def test_criteria_thresholds_come_from_the_config():
    c = criteria()
    assert (c["GAZE_ON_SCRIPT"].threshold, c["GAZE_ON_SCRIPT"].bad) == (
        DEFAULT.script_run_ms,
        DEFAULT.script_run_bad_ms,
    )
    assert (c["GAZE_LOW_EYE_CONTACT"].threshold, c["GAZE_UNMEASURABLE"].threshold) == (0.3, 0.5)
    custom = criteria(GazeConfig(away_run_ms=2500))
    assert (custom["GAZE_AWAY"].threshold, custom["GAZE_AWAY"].onset_lag_ms) == (2500, 2500)


def test_criteria_lags_follow_the_windows():
    c = criteria()
    lags = {k: (v.onset_lag_ms, v.offset_lag_ms) for k, v in c.items()}
    assert lags == {
        "GAZE_ON_SCRIPT": (3000, 0),
        "GAZE_ON_SCREEN": (5000, 0),
        "GAZE_AWAY": (2000, 0),
        "GAZE_LOW_EYE_CONTACT": (21_000, 9000),
        "GAZE_UNMEASURABLE": (2500, 15_000),
    }


@pytest.mark.parametrize("issue", ["GAZE_ON_SCRIPT", "GAZE_ON_SCREEN", "GAZE_AWAY"])
def test_a_run_problem_appears_after_its_onset_lag(issue):
    state = {"GAZE_ON_SCRIPT": "BOTTOM", "GAZE_ON_SCREEN": "SCREEN", "GAZE_AWAY": "OTHER"}[issue]
    lag = criteria()[issue].onset_lag_ms // 1000
    before = run(secs(("CAMERA", 30 - lag + 1), (state, lag - 1)), 30_000)
    at = run(secs(("CAMERA", 30 - lag), (state, lag)), 30_000)
    assert issue not in [i.issue_type for i in before.issues]
    assert issue in [i.issue_type for i in at.issues]


def test_summarize_handles_sparse_keys_and_independent_zero_denominators():
    g = summarize({"GAZE": {"camera_ms": 1000, "measured_ms": 1000, "total_ms": 1000}})["GAZE"]
    assert g == {
        "audience_ratio": 1.0, "script_ratio": 0.0, "screen_ratio": 0.0, "away_ratio": 0.0,
        "reading_ratio": None, "reading_runs": 0, "measured_ratio": 1.0,
    }  # fmt: skip


def test_summarize_of_incremental_tallies_matches_all_same_window_ratios():
    # 코치처럼 돌려받은 커서로 10초마다 부르고 tally 를 더한다
    pattern = ("CAMERA", "BOTTOM", "SCREEN", "OTHER", "UNCERTAIN")
    records = secs(*[(s, 1) for s in pattern * 6])
    voiced = spoken(0, 30_000)
    total: dict[str, int] = {}
    since = 0
    for t in (10_000, 20_000, 30_000):
        r = run([x for x in records if x["t_ms"] < t], t, since, voiced)
        for k, v in totals(r).items():
            total[k] = total.get(k, 0) + v
        since = r.counted_until_ms
    g = summarize({"GAZE": total})["GAZE"]
    assert (g["audience_ratio"], g["script_ratio"], g["screen_ratio"], g["away_ratio"]) == (
        0.25,
    ) * 4
    assert (g["reading_ratio"], g["measured_ratio"], g["reading_runs"]) == (0.2, 0.8, 0)
    m = r.metrics
    assert (m.audience_ratio, m.script_ratio, m.screen_ratio, m.away_ratio, m.reading_ratio) == (
        g["audience_ratio"],
        g["script_ratio"],
        g["screen_ratio"],
        g["away_ratio"],
        g["reading_ratio"],
    )


def test_criteria_recomputes_lags_and_bad_from_custom_config():
    c = criteria(
        GazeConfig(
            short_window_ms=10_000, recent_window_ms=30_000, script_short_ratio=0.8,
            low_eye_contact_ratio=0.4, low_eye_contact_min_measured_ms=20_000,
            min_measured_ratio=0.6, away_run_bad_ms=9000,
        )
    )  # fmt: skip
    assert c["GAZE_ON_SCRIPT"].onset_lag_ms == 8000
    assert (c["GAZE_LOW_EYE_CONTACT"].onset_lag_ms, c["GAZE_LOW_EYE_CONTACT"].offset_lag_ms) == (
        20_000,
        12_000,
    )
    assert (c["GAZE_UNMEASURABLE"].onset_lag_ms, c["GAZE_UNMEASURABLE"].offset_lag_ms) == (
        4000,
        18_000,
    )
    assert c["GAZE_AWAY"].bad == 9000
