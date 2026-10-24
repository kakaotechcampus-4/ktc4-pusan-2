"""입력 · 출력 계약 — pydantic 모델, 사전과 기준값 (filler.md 5 · 9절)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from filler.config import DEFAULT, FillerConfig, normalize
from filler.schemas import FillerInput, JudgmentResult
from filler.version import FEATURE_VERSION

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


def test_missing_stt_status_is_not_ok():
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
