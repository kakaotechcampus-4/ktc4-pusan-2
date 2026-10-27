"""rescore_evaluation (비슷한 말 사용자 확인 뒤 재계산) 의 입력 검사."""

import pytest

from pitch_coach_ai.features.script_coverage.shared.rubric import EvaluationRubric, RubricMeta
from pitch_coach_ai.features.script_coverage.stt_evaluation.confirm import (
    CONFIRM_TEXT,
    rescore_evaluation,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SimilarItem,
    SlideEvaluation,
)


def _rubric(rubric_id: str) -> EvaluationRubric:
    meta = RubricMeta(
        rubric_id=rubric_id,
        script_name="s",
        slide_number=1,
        content_hash="h",
        rubric_schema_version="1.2",
        llm_model="m",
        llm_config_hash="a",
    )
    return EvaluationRubric.model_construct(meta=meta)


def _evaluation(**overrides) -> SlideEvaluation:
    fields = dict(
        take_id="T",
        script_name="s",
        slide_number=1,
        rubric_id="rid1",
        scores={},
        sentences=[],
        key_points=[],
        critical_facts=[],
        similar_items=[],
        fidelity={},
        alignments=[],
        verification={},
        stt_sentences=[],
        stt_text="말했다",
    )
    return SlideEvaluation(**(fields | overrides))


def test_rescore_rejects_changed_rubric():
    with pytest.raises(ValueError, match="평가 기준이 바뀌었다"):
        rescore_evaluation(_evaluation(rubric_id="other"), {}, _rubric("rid1"))


def _item(item_id: str) -> SimilarItem:
    return SimilarItem(
        item_id=item_id,
        kind="word",
        script_text="좌석",
        stt_text="자석",
        script_sentence_index=0,
        script_span=(0, 2),
        stt_sentence_index=0,
        stt_span=(0, 2),
        stt_raw_span=None,
        key_point_ids=[],
        signals=[],
        rule_guess="asr_error",
    )


def _full_rubric() -> EvaluationRubric:
    meta = _rubric("rid1").meta
    return EvaluationRubric(
        meta=meta,
        normalized_script="",
        sentences=[],
        sentence_roles=[],
        core_claim="",
        key_points=[],
        critical_facts=[],
        keywords=[],
        warnings=[],
    )


def _with_items(applied=None) -> SlideEvaluation:
    return _evaluation(
        similar_items=[_item("R1"), _item("R2"), _item("R3")],
        applied_answers=applied or {},
        fidelity={"fidelity": 1.0},
    )


def _rescore(evaluation, answers):
    return rescore_evaluation(evaluation, answers, _full_rubric())


def test_rescore_accepts_empty_stt_text():
    """발표에서 넘어간 장(빈 STT)도 정상으로 다시 계산한다."""
    assert _rescore(_evaluation(stt_text=""), {}).applied_answers == {}


def test_rescore_without_answers_recomputes_same_result():
    first = _rescore(_with_items(), {})
    assert first.applied_answers == {}
    assert first.scores["similar_words"] == 3
    assert _rescore(_with_items(), {}).model_dump() == first.model_dump()


def test_rescore_accumulates_answers_and_drops_unknown_items():
    result = _rescore(_with_items(), {"R1": "as_script", "R9": "as_stt", "R2": "maybe"})
    assert result.applied_answers == {"R1": "as_script"}  # 모르는 항목 · 모르는 답은 버린다
    assert result.scores["similar_words"] == 2  # 답하지 않은 항목만 보류로 센다


def test_rescore_keeps_first_answer_for_applied_items():
    previous = _with_items({"R1": "as_script"})
    # 같은 답을 다시 보내도, 다른 답이 와도 처음 답을 유지하고 새 항목 답만 더한다
    same = _rescore(previous, {"R1": "as_script", "R2": "as_stt"})
    other = _rescore(previous, {"R1": "as_stt"})
    assert same.applied_answers == {"R1": "as_script", "R2": "as_stt"}
    assert other.applied_answers == {"R1": "as_script"}
    assert same.scores["similar_words"] == 1 and other.scores["similar_words"] == 2
    assert previous.applied_answers == {"R1": "as_script"}  # 받은 채점은 바꾸지 않는다


def test_confirm_text_answers():
    assert list(CONFIRM_TEXT) == ["as_script", "as_stt"]
