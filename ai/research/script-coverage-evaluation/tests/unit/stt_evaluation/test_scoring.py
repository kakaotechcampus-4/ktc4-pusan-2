"""점수 계산. 기대값은 노트북 원본 코드(셀 25)를 같은 입력으로 돌려 얻은 값이다."""

from types import SimpleNamespace

from script_coverage.stt_evaluation.schemas import FactCheck, KeyPointResult, SimilarItem
from script_coverage.stt_evaluation.scoring import slide_scores, take_scores


def _kp(key_point_id, importance, status):
    return KeyPointResult(
        key_point_id=key_point_id,
        content="c",
        importance=importance,
        sentence_indices=[0],
        status=status,
        first_status=status,
        evidence=[],
        evidence_text="",
        reason="",
        lexical_coverage=0.0,
        fact_statuses={},
    )


def _fact(fact_id, importance, status):
    return FactCheck(
        fact_id=fact_id,
        type="number",
        value="v",
        normalized="n",
        importance=importance,
        key_point_ids=[],
        status=status,
    )


def _item(item_id, kind):
    return SimilarItem(
        item_id=item_id,
        kind=kind,
        script_text="a",
        stt_text="b",
        script_sentence_index=0,
        script_span=(0, 1),
        stt_sentence_index=0,
        stt_span=(0, 1),
        stt_raw_span=None,
        key_point_ids=[],
        signals=[],
        rule_guess="asr_error",
    )


def test_slide_scores_weighting():
    key_points = [
        _kp("K1", "critical", "covered"),
        _kp("K2", "high", "partial"),
        _kp("K3", "normal", "contradicted"),
        _kp("K4", "normal", "missing"),
    ]
    facts = [
        _fact("F1", "critical", "matched"),
        _fact("F2", "high", "approximate"),
        _fact("F3", "normal", "missing"),
        _fact("F4", "normal", "sound_alike"),  # 판단 보류: 분자 · 분모에서 뺀다
        _fact("F5", "high", "mismatched"),
    ]
    items = [_item("R1", "word"), _item("R2", "number"), _item("R3", "word")]
    scores = slide_scores(key_points, facts, {"fidelity": 0.81, "script_tokens": 17}, items)
    assert scores == {
        "content_coverage": 0.5,
        "critical_fact_accuracy": 0.5,
        "script_fidelity": 0.81,
        "similar_words": 2,
        "similar_numbers": 1,
        "_weights": {"key_points": 7, "facts": 8, "script_tokens": 17},
    }


def test_slide_scores_empty():
    assert slide_scores([], [], {"fidelity": 0.0}, []) == {
        "content_coverage": 0.0,
        "critical_fact_accuracy": None,
        "script_fidelity": 0.0,
        "similar_words": 0,
        "similar_numbers": 0,
        "_weights": {"key_points": 0, "facts": 0, "script_tokens": 0},
    }


def test_slide_scores_negative_coverage_is_clamped_to_zero():
    scores = slide_scores([_kp("K", "normal", "contradicted")], [], {"fidelity": 0.5}, [])
    assert scores["content_coverage"] == 0.0


def test_take_scores_is_weighted_average_of_slides():
    slides = [
        SimpleNamespace(
            scores={
                "content_coverage": 0.5,
                "critical_fact_accuracy": 1.0,
                "script_fidelity": 0.8,
                "similar_words": 1,
                "similar_numbers": 0,
                "_weights": {"key_points": 4, "facts": 2, "script_tokens": 10},
            }
        ),
        SimpleNamespace(
            scores={
                "content_coverage": 1.0,
                "critical_fact_accuracy": None,
                "script_fidelity": 0.4,
                "similar_words": 0,
                "similar_numbers": 2,
                "_weights": {"key_points": 2, "facts": 0, "script_tokens": 30},
            }
        ),
    ]
    assert take_scores(slides) == {
        "content_coverage": 0.667,
        "critical_fact_accuracy": 1.0,
        "script_fidelity": 0.5,
        "similar_words": 1,
        "similar_numbers": 2,
    }


def test_take_scores_empty():
    assert take_scores([]) == {
        "content_coverage": None,
        "critical_fact_accuracy": None,
        "script_fidelity": None,
        "similar_words": 0,
        "similar_numbers": 0,
    }
