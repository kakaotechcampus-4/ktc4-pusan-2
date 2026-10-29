"""summarize — 장 · Take 의 tally 합계 → 군더더기 지표 (filler.md 5-3 · 6-4 · 14절)."""

from __future__ import annotations

import pytest

from pitch_coach_ai.features.filler.core import summarize
from pitch_coach_ai.features.filler.schemas import FillerSummary

from .helpers import fillers, run, total

FULL = {
    "filler_count": 7,
    "filler_t1": 4,
    "filler_t3": 3,
    "filler_word:음": 4,
    "filler_word:그니까": 2,
    "filler_word:좀": 1,
    "elapsed_ms": 175_000,
    "total_ms": 180_412,
}


def test_summarize_turns_sums_into_the_take_metrics():
    assert summarize({"FILLER": FULL}) == {
        "FILLER": {
            "filler_count": 7,
            "filler_per_min": 2.4,
            "by_tier": {"T1": 4, "T2": 0, "T3": 3},
            "by_word": {"음": 4, "그니까": 2, "좀": 1},
            "measured_ratio": 0.97,
        }
    }


def test_summarize_output_is_a_filler_summary_model():
    FillerSummary.model_validate(summarize({"FILLER": FULL})["FILLER"])


def test_per_min_is_null_when_no_time_was_measured():
    out = summarize({"FILLER": {"filler_count": 3, "filler_t1": 3, "total_ms": 4_000}})
    assert out["FILLER"]["filler_per_min"] is None
    assert out["FILLER"]["filler_count"] == 3


@pytest.mark.parametrize("tally", [{"FILLER": {}}, {}, {"FILLER": {"elapsed_ms": 0}}])
def test_summarize_of_nothing_gives_zeros_and_null_per_min(tally):
    assert summarize(tally) == {
        "FILLER": {
            "filler_count": 0,
            "filler_per_min": None,
            "by_tier": {"T1": 0, "T2": 0, "T3": 0},
            "by_word": {},
            "measured_ratio": 0.0,
        }
    }


def test_measured_ratio_is_zero_when_the_total_time_is_zero():
    out = summarize({"FILLER": {"elapsed_ms": 1_000, "total_ms": 0}})
    assert out["FILLER"]["measured_ratio"] == 0.0


def test_measured_ratio_is_rounded_to_four_decimals():
    out = summarize({"FILLER": {"elapsed_ms": 1_000, "total_ms": 3_000}})
    assert out["FILLER"]["measured_ratio"] == 0.3333


def test_by_word_is_sorted_by_count_then_by_word():
    tally = {
        "FILLER": {
            "filler_count": 6,
            "filler_word:저": 1,
            "filler_word:가": 2,
            "filler_word:음": 3,
            "filler_word:나": 2,
        }
    }
    assert list(summarize(tally)["FILLER"]["by_word"].items()) == [
        ("음", 3),
        ("가", 2),
        ("나", 2),
        ("저", 1),
    ]


def test_by_word_keeps_the_word_even_when_it_contains_a_colon():
    tally = {"FILLER": {"filler_count": 1, "filler_word:음:": 1}}
    assert summarize(tally)["FILLER"]["by_word"] == {"음:": 1}


def test_summarize_of_judged_tallies_counts_the_fillers_that_were_judged():
    r = run(30_000, fillers(4), since_ms=29_000)
    sums = {k: total(r.tally, k) for k in ("filler_count", "filler_t1", "elapsed_ms", "total_ms")}
    sums["filler_word:음"] = total(r.tally, "filler_word:음")
    out = summarize({"FILLER": sums})["FILLER"]
    assert (out["filler_count"], out["by_tier"]["T1"], out["by_word"]) == (4, 4, {"음": 4})
    assert out["measured_ratio"] == 1.0
    assert out["filler_per_min"] == 240.0
