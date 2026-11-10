"""실패 장 간이 값: 문장 3단계 · 구간 · 수치 · 평가 기준 없는 장."""

import pytest

from pitch_coach_ai.features.script_coverage.stt_evaluation.align import (
    align_sentences,
    fidelity_excluding,
    positioned_tokens,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.config import (
    FALLBACK_SENTENCE_HI,
    FALLBACK_SENTENCE_LO,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.fact_check import check_critical_facts
from pitch_coach_ai.features.script_coverage.stt_evaluation.fallback import (
    lexical_fallback,
    no_rubric_fallback,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.normalize import normalize_stt
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import Alignment

from ..fakes import deck_rubrics
from ..shared.slides import SLIDES

RUBRIC = deck_rubrics((4,))[0]
SCRIPT = SLIDES[("가상대본1", 4)]


def _rule_inputs(stt: str):
    from pitch_coach_ai.features.script_coverage.shared.text import normalize_script

    script_norm, stt_norm = normalize_script(RUBRIC.normalized_script), normalize_stt(stt)
    sp, tp = positioned_tokens(script_norm), positioned_tokens(stt_norm)
    alignments = align_sentences(
        script_norm,
        [(t.key, t.sentence) for t in sp],
        stt_norm,
        [(t.key, t.sentence) for t in tp],
        RUBRIC.sentence_roles,
    )
    return (
        alignments,
        check_critical_facts(RUBRIC, stt_norm, alignments),
        fidelity_excluding(RUBRIC, sp, tp, []),
    )


def test_full_stt_is_mostly_and_empty_is_little():
    alignments, facts, fidelity = _rule_inputs(SCRIPT)
    f = lexical_fallback(
        RUBRIC,
        alignments,
        facts,
        fidelity,
        stt_integrity="complete",
        speech_mix=None,
        reason_code="LLM_TIMEOUT",
    )
    assert f.method == "lexical" and f.delivery_band == "mostly" and f.lexical_coverage == 1.0
    assert f.reason_codes == ["LLM_TIMEOUT"] and f.stt_integrity == "complete"
    assert f.script_recall.state == "estimated" and f.script_recall.value == 1.0
    assert f.facts is not None and f.fact_accuracy.state == "estimated"
    alignments, facts, fidelity = _rule_inputs("")
    f = lexical_fallback(
        RUBRIC,
        alignments,
        facts,
        fidelity,
        stt_integrity="unknown",
        speech_mix=None,
        reason_code="LLM_ERROR",
    )
    assert (f.delivery_band, f.lexical_coverage) == ("little", 0.0)
    assert f.fact_accuracy.value == 0.0


HI, LO = FALLBACK_SENTENCE_HI, FALLBACK_SENTENCE_LO
UNDER_HI, UNDER_LO = round(HI - 0.01, 2), round(LO - 0.01, 2)


@pytest.mark.parametrize(
    ("coverages", "value", "band"),
    [
        ([HI, HI], 1.0, "mostly"),
        ([HI, LO], 0.75, "mostly"),
        ([UNDER_HI, LO], 0.5, "about_half"),
        ([UNDER_LO, UNDER_LO], 0.0, "little"),
        ([HI, UNDER_LO, UNDER_LO], 0.333, "about_half"),
        ([HI, UNDER_LO, UNDER_LO, UNDER_LO], 0.25, "little"),
    ],
)
def test_sentence_steps_and_bands(coverages, value, band):
    alignments = [
        Alignment(sentence_index=i, stt_ids=[0], coverage=c) for i, c in enumerate(coverages)
    ]
    rubric = RUBRIC.model_copy(update={"sentence_roles": ["detail"] * len(coverages)})
    f = lexical_fallback(
        rubric,
        alignments,
        [],
        {"script_tokens": 0},
        stt_integrity="unknown",
        speech_mix=None,
        reason_code="X",
    )
    assert (f.lexical_coverage, f.delivery_band) == (value, band)
    assert f.facts is None and f.fact_accuracy is None and f.script_recall is None


def test_no_alignments_is_null():
    f = lexical_fallback(
        RUBRIC,
        [],
        [],
        {"script_tokens": 0},
        stt_integrity="unknown",
        speech_mix=None,
        reason_code="X",
    )
    assert f.lexical_coverage is None and f.delivery_band is None


def test_no_rubric_fallback():
    f = no_rubric_fallback(SCRIPT, SCRIPT, stt_integrity="complete")
    assert f.method == "lexical_no_rubric" and f.reason_codes == ["RUBRIC_MISSING"]
    assert f.delivery_band == "mostly" and f.speech_mix is None
    assert f.script_recall.value == 1.0
    empty = no_rubric_fallback(SCRIPT, "", stt_integrity="unknown")
    assert empty.delivery_band == "little"
    blank = no_rubric_fallback("", "말", stt_integrity="unknown")
    assert (blank.lexical_coverage, blank.delivery_band, blank.script_recall) == (None, None, None)
