"""입력 · 출력 계약 — 모델, criteria_version, 사전, criteria · keyterms (filler.md 5 · 9절)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from filler.config import DEFAULT, FillerConfig, normalize
from filler.core import criteria, criteria_version, judge, keyterms
from filler.schemas import FillerInput, JudgmentResult
from filler.version import FEATURE_VERSION
from tests.helpers import CFG, fillers, inputs, run, word

T = 30_000
REQUIRED = {"t_ms": 1000, "since_ms": 0, "words_since_ms": 0}

# ── 입력 ───────────────────────────────────────────────────────────────────────


def test_unknown_input_fields_are_ignored():
    inp = FillerInput.model_validate(
        {**REQUIRED, "note": "x", "words": [{"word": "음", "start_ms": 0, "end_ms": 1, "x": 1}]}
    )
    assert inp.words[0].word == "음"


@pytest.mark.parametrize("field", ["t_ms", "since_ms", "words_since_ms"])
def test_time_and_cursors_are_required(field):
    raw = dict(REQUIRED)
    del raw[field]
    with pytest.raises(ValidationError):
        FillerInput.model_validate(raw)


def test_unknown_stt_status_is_rejected():
    with pytest.raises(ValidationError):
        FillerInput.model_validate({**REQUIRED, "stt_status": "lost"})


def test_input_model_keeps_a_missing_stt_status_as_none():
    assert FillerInput.model_validate(REQUIRED).stt_status is None


def test_optional_input_fields_have_empty_defaults():
    inp = FillerInput.model_validate(REQUIRED)
    assert (inp.words, inp.utterance_ends, inp.take_end_ms, inp.stt_ok_since_ms) == (
        [],
        [],
        None,
        0,
    )


# ── 출력 ───────────────────────────────────────────────────────────────────────


def test_output_models_reject_unknown_fields():
    dumped = {
        "t_ms": 1000, "counted_until_ms": 1000, "words_counted_until_ms": 0,
        "criteria_version": "filler-1.0+000000000000", "measurable": True, "state": "NORMAL",
        "words": [], "metrics": {"recent_filler_count": 0, "filler_per_min": None},
        "tally": [], "issues": [],
    }  # fmt: skip
    assert JudgmentResult.model_validate(dumped).area == "FILLER"
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate({**dumped, "extra": 1})


def test_feature_version_is_filler_1_0():
    assert FEATURE_VERSION == "filler-1.0"


# ── 사전과 기준값 ──────────────────────────────────────────────────────────────


def test_default_dictionary_has_the_documented_sizes():
    assert (len(DEFAULT.t1_words), len(DEFAULT.t2_words), len(DEFAULT.t3_words)) == (8, 11, 36)
    assert len(DEFAULT.keyterms) == 15


def test_default_gap_and_frequency_values():
    assert (DEFAULT.gap_ms, DEFAULT.sentence_gap_ms) == (400, 1_000)
    assert (DEFAULT.window_ms, DEFAULT.frequent_count, DEFAULT.frequent_bad) == (60_000, 6, 15)


def test_config_rejects_a_word_in_two_layers():
    with pytest.raises(ValidationError):
        FillerConfig(t3_words=(*DEFAULT.t3_words, "그"))


def test_config_rejects_a_duplicate_word_inside_a_layer():
    with pytest.raises(ValidationError):
        FillerConfig(t3_words=(*DEFAULT.t3_words, "좀"))


@pytest.mark.parametrize("bad", ["", "음.", " 음", "음!"])
def test_config_rejects_words_that_are_not_normalized(bad):
    with pytest.raises(ValidationError):
        FillerConfig(t3_words=(*DEFAULT.t3_words, bad))


def test_config_rejects_a_keyterm_outside_the_dictionary():
    with pytest.raises(ValidationError):
        FillerConfig(keyterms=(*DEFAULT.keyterms, "좀"))


def test_config_rejects_a_keyterm_from_the_t3_layer_and_unknown_words():
    with pytest.raises(ValidationError):
        FillerConfig(keyterms=(*DEFAULT.keyterms, "없는단어"))


def test_config_rejects_duplicate_keyterms():
    with pytest.raises(ValidationError):
        FillerConfig(keyterms=(*DEFAULT.keyterms, "음"))


def test_config_requires_every_t1_word_to_be_a_keyterm():
    with pytest.raises(ValidationError):
        FillerConfig(keyterms=tuple(w for w in DEFAULT.keyterms if w != "으음"))


def test_config_is_frozen_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        DEFAULT.gap_ms = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        FillerConfig(unknown=1)  # type: ignore[call-arg]


def test_normalize_strips_outer_spaces_and_punctuation_only():
    assert normalize(" 어어. ") == "어어"
    assert normalize("“음…”") == "음"
    assert normalize("어-음") == "어-음"
    assert normalize("...") == ""


# ── judge 입력 ─────────────────────────────────────────────────────────────────


def test_unknown_input_fields_do_not_change_the_result():
    words = [word("음", 1000, 1300)]
    plain = judge(inputs(T, words), T, CFG)
    extra = judge(inputs(T, words, note="x", extra={"a": 1}), T, CFG)
    assert extra == plain


@pytest.mark.parametrize("field", ["t_ms", "since_ms", "words_since_ms"])
def test_judge_requires_time_and_cursors(field):
    raw = inputs(T)
    del raw[field]
    with pytest.raises(ValidationError):
        judge(raw, T)


def test_judge_rejects_an_unknown_stt_status():
    with pytest.raises(ValidationError):
        judge(inputs(T, stt_status="lost"), T)


def test_missing_stt_status_is_not_ok():
    raw = inputs(T, [word("음", 1000, 1300)])
    del raw["stt_status"]
    [result] = judge(raw, T)
    assert (result.measurable, result.state, result.words) == (False, "UNKNOWN", [])


def test_t_ms_argument_must_equal_the_input_t_ms():
    with pytest.raises(ValueError, match="t_ms"):
        judge(inputs(T), T + 1)


def test_input_words_may_omit_the_optional_fields():
    raw = {"t_ms": T, "since_ms": T - 1000, "words_since_ms": 0, "stt_status": "ok"}
    [result] = judge(raw, T)
    assert result.words == [] and result.state == "NORMAL"


# ── judge 출력 ─────────────────────────────────────────────────────────────────

RESULT_KEYS = [
    "evaluator", "area", "t_ms", "counted_until_ms", "words_counted_until_ms",
    "criteria_version", "measurable", "state", "words", "metrics", "tally", "issues",
]  # fmt: skip


def test_output_json_has_exactly_the_contract_keys():
    result = run(T, fillers(7), take_end_ms=T)
    dumped = result.model_dump(mode="json")
    assert list(dumped) == RESULT_KEYS
    assert list(dumped["words"][0]) == ["start_ms", "end_ms", "word", "tier", "is_filler", "reason"]
    assert list(dumped["metrics"]) == ["recent_filler_count", "filler_per_min"]
    assert list(dumped["tally"][0]) == ["t_ms", "values"]
    assert list(dumped["issues"][0]) == [
        "issue_type", "area", "severity", "confidence", "persistence_sec",
        "threshold", "bad", "evidence", "actionable",
    ]  # fmt: skip


def test_result_and_issue_areas_are_filler():
    result = run(T, fillers(7))
    assert (result.evaluator, result.area) == ("filler", "FILLER")
    assert all(i.area == "FILLER" for i in result.issues)
    assert all(0.0 <= i.severity <= 1.0 for i in result.issues)


def test_judge_returns_one_result_in_a_list():
    assert len(judge(inputs(T), T)) == 1


@pytest.mark.parametrize(
    "raw",
    [
        inputs(T, [word("음", 1000, 1300)]),
        inputs(T, fillers(3), utterance_ends=[500], take_end_ms=T),
    ],
    ids=["one-word", "fillers-with-signals"],
)
def test_model_input_and_dict_input_give_the_same_result(raw):
    assert judge(FillerInput.model_validate(raw), T, CFG) == judge(raw, T, CFG)


def test_output_models_reject_unknown_fields_on_nested_models_too():
    dumped = run(T, fillers(1)).model_dump()
    dumped["words"][0]["extra"] = 1
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate(dumped)


# ── criteria_version ───────────────────────────────────────────────────────────


def test_criteria_version_is_the_feature_version_plus_a_config_hash():
    assert re.fullmatch(r"^filler-1\.0\+[0-9a-f]{12}$", run(T).criteria_version)
    assert run(T).criteria_version.startswith(FEATURE_VERSION + "+")


def test_criteria_version_changes_only_when_the_config_changes():
    same = FillerConfig(**CFG.model_dump())
    other = FillerConfig(**{**CFG.model_dump(), "gap_ms": 500})
    assert run(T, config=same).criteria_version == run(T).criteria_version
    assert run(T, config=other).criteria_version != run(T).criteria_version


def test_criteria_version_changes_when_the_dictionary_changes():
    other = FillerConfig(t3_words=(*DEFAULT.t3_words, "뭐랄까요"))
    assert criteria_version(other) != criteria_version(DEFAULT)


# ── criteria · keyterms ────────────────────────────────────────────────────────


def test_criteria_gives_the_frequent_issue_with_the_config_values():
    c = criteria()
    assert c.keys() == {"FILLER_FREQUENT"}
    item = c["FILLER_FREQUENT"]
    assert (item.metric, item.direction) == ("recent_filler_count", "HIGHER_IS_WORSE")
    assert (item.threshold, item.bad) == (6.0, 15.0)
    assert (item.onset_lag_ms, item.offset_lag_ms) == (30_000, 30_000)


def test_criteria_follows_a_changed_config():
    item = criteria(FillerConfig(frequent_count=4, frequent_bad=10))["FILLER_FREQUENT"]
    assert (item.threshold, item.bad) == (4.0, 10.0)


def test_issue_threshold_and_bad_value_match_the_criteria():
    [issue] = run(T, fillers(7), config=DEFAULT).issues
    item = criteria()["FILLER_FREQUENT"]
    assert (issue.threshold, issue.bad) == (item.threshold, item.bad)


def test_keyterms_are_the_fifteen_words_in_the_agreed_order():
    assert keyterms() == [
        "음", "어", "아", "에", "으", "그", "저", "이제", "인제", "뭐", "자", "저기",
        "음음", "어어", "으음",
    ]  # fmt: skip


def test_keyterms_returns_a_copy_that_does_not_change_the_config():
    keyterms().append("좀")
    assert len(keyterms()) == 15
