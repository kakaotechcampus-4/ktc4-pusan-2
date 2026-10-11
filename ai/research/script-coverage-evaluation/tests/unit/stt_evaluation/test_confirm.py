"""rescore_evaluation (비슷한 말 사용자 확인 뒤 재계산) 의 입력 검사."""

import pytest

from script_coverage.shared.rubric import EvaluationRubric, RubricMeta
from script_coverage.stt_evaluation.confirm import CONFIRM_TEXT, rescore_evaluation
from script_coverage.stt_evaluation.schemas import SlideEvaluation


def _rubric(rubric_id: str) -> EvaluationRubric:
    meta = RubricMeta(
        rubric_id=rubric_id,
        script_name="s",
        slide_number=1,
        content_hash="h",
        rubric_schema_version="1.1",
        llm_model="m",
        llm_config_hash="a",
        created_at="t",
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
        created_at="c",
    )
    return SlideEvaluation(**(fields | overrides))


def test_rescore_rejects_changed_rubric():
    with pytest.raises(ValueError, match="평가 기준이 바뀌었다"):
        rescore_evaluation(_evaluation(rubric_id="other"), {}, _rubric("rid1"))


def test_rescore_rejects_evaluation_without_stt_text():
    with pytest.raises(ValueError, match="STT 텍스트가 없는 예전 평가"):
        rescore_evaluation(_evaluation(stt_text=""), {}, _rubric("rid1"))


def test_confirm_text_answers():
    assert list(CONFIRM_TEXT) == ["as_script", "as_stt"]
