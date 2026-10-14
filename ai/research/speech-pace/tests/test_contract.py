"""입력 · 출력 계약 — pydantic 모델, criteria_version, summarize · criteria (pace.md 5 · 9절)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pace.config import DEFAULT, PaceConfig
from pace.core import criteria, judge, summarize
from pace.schemas import JudgmentResult, PaceInput
from pace.version import FEATURE_VERSION
from tests.helpers import CFG, inputs, run, steady, total

T = 60_000

# ── 입력 ───────────────────────────────────────────────────────────────────────


def test_unknown_input_fields_are_ignored():
    words = steady(T, chars=3, dur_ms=600)
    extra = inputs(words, t_ms=T, utterance_ends=[T - 500], take_end_ms=None, note="x")
    assert judge(extra, T, CFG) == judge(inputs(words), T, CFG)


def test_unknown_stt_status_is_rejected():
    with pytest.raises(ValidationError):
        judge(inputs(stt_status="lost"), T)


def test_missing_stt_status_is_not_ok():
    raw = inputs(steady(T, chars=3, dur_ms=600), since_ms=T - 1000)
    del raw["stt_status"]
    [r] = judge(raw, T)
    assert r.state == "UNKNOWN" and total(r.tally, "ok_ms") == 0


@pytest.mark.parametrize("field", ["since_ms", "words_since_ms", "fillers"])
def test_cursors_and_fillers_are_required(field):
    raw = inputs()
    del raw[field]
    with pytest.raises(ValidationError):
        judge(raw, T)


def test_a_filler_mark_without_is_filler_is_pending():
    words = steady(3000, chars=3, dur_ms=600, seconds=3)
    mark = {"start_ms": words[1]["start_ms"], "end_ms": words[1]["end_ms"]}
    assert run(3000, words, fillers=[mark]).words_counted_until_ms == words[0]["end_ms"]


def test_a_filler_mark_with_is_filler_false_is_an_ordinary_word():
    words = steady(3000, chars=3, dur_ms=600, seconds=3)
    mark = {"start_ms": words[1]["start_ms"], "end_ms": words[1]["end_ms"], "is_filler": False}
    assert run(3000, words, fillers=[mark]) == run(3000, words)


def test_judge_accepts_a_validated_input_model_as_well_as_a_dict():
    raw = inputs(steady(T, chars=3, dur_ms=600))
    assert judge(PaceInput.model_validate(raw), T, CFG) == judge(raw, T, CFG)


# ── 출력 ───────────────────────────────────────────────────────────────────────


def test_output_models_reject_unknown_fields():
    dumped = run(T, steady(T, chars=3, dur_ms=450)).model_dump()
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate({**dumped, "extra": 1})


def test_criteria_version_is_the_feature_version_plus_a_config_hash():
    version = run(T).criteria_version
    feature, plus, digest = version.partition("+")
    assert feature == FEATURE_VERSION == "pace-1.0"
    assert plus and len(digest) == 12


def test_criteria_version_changes_only_when_the_config_changes():
    same = PaceConfig(**CFG.model_dump())
    other = PaceConfig(**{**CFG.model_dump(), "pause_min_ms": 2_500})
    assert run(T, config=same).criteria_version == run(T).criteria_version
    assert run(T, config=other).criteria_version != run(T).criteria_version


# ── summarize ──────────────────────────────────────────────────────────────────


def test_summarize_divides_chars_by_speaking_time_and_ok_time_by_total_time():
    tally = {
        "SPEED": {
            "chars": 482,
            "speak_ms": 90_800,
            "pause_count": 3,
            "pause_ms": 7_400,
            "ok_ms": 176_000,
            "total_ms": 182_000,
        }
    }
    assert summarize(tally) == {"SPEED": {"cpm": 318.5, "pause_count": 3, "measured_ratio": 0.967}}


@pytest.mark.parametrize("tally", [{"SPEED": {"total_ms": 4_000}}, {"SPEED": {}}, {}])
def test_summarize_with_zero_denominators_gives_null_cpm_and_zero_ratio(tally):
    assert summarize(tally) == {"SPEED": {"cpm": None, "pause_count": 0, "measured_ratio": 0.0}}


def test_summarize_of_judged_tallies_gives_the_cpm_of_the_counted_words():
    r = run(T, steady(T, chars=3, dur_ms=450), since_ms=T - 1000)
    sums = {k: total(r.tally, k) for k in ("chars", "speak_ms", "ok_ms", "total_ms")}
    assert summarize({"SPEED": sums})["SPEED"]["cpm"] == 400.0


# ── criteria ───────────────────────────────────────────────────────────────────


def test_criteria_gives_both_issues_with_the_config_values():
    c = criteria()
    assert c.keys() == {"PACE_FAST", "PACE_SLOW"}
    fast, slow = c["PACE_FAST"], c["PACE_SLOW"]
    assert (fast.metric, fast.direction) == ("cpm", "HIGHER_IS_WORSE")
    assert (fast.threshold, fast.bad) == (DEFAULT.fast_cpm, DEFAULT.fast_cpm_bad)
    assert (slow.metric, slow.direction) == ("cpm", "LOWER_IS_WORSE")
    assert (slow.threshold, slow.bad) == (DEFAULT.slow_cpm, DEFAULT.slow_cpm_bad)
    for item in (fast, slow):
        assert (item.onset_lag_ms, item.offset_lag_ms) == (
            DEFAULT.onset_lag_ms,
            DEFAULT.offset_lag_ms,
        )


def test_issue_threshold_and_bad_value_match_the_criteria():
    for chars, dur_ms, name in ((3, 450, "PACE_FAST"), (4, 1000, "PACE_SLOW")):
        [issue] = run(T, steady(T, chars=chars, dur_ms=dur_ms), config=DEFAULT).issues
        c = criteria()[name]
        assert (issue.issue_type, issue.threshold, issue.bad) == (name, c.threshold, c.bad)
