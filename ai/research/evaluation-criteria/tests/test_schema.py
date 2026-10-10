import pytest
from pydantic import ValidationError

from evaluation_criteria.schema import Criteria, EvaluationCriteriaAnalysis

CRITERIA_FIELDS = [
    "speed",
    "pause",
    "volume",
    "filler",
    "gaze",
    "script_used",
    "script_dependency",
    "script_similarity",
]
EMPTY_VALUES = dict.fromkeys(CRITERIA_FIELDS)


def test_criteria_fields_fixed():
    # 프롬프트의 평가 요소 표(8개)와 스키마 필드가 어긋나면 안 됨
    assert list(Criteria.model_fields) == CRITERIA_FIELDS


def test_all_fields_required():
    # json_schema strict 모드: 모든 필드가 required여야 함 (null은 값으로 허용)
    criteria_schema = Criteria.model_json_schema()
    assert set(criteria_schema["required"]) == set(CRITERIA_FIELDS)

    analysis_schema = EvaluationCriteriaAnalysis.model_json_schema()
    assert set(analysis_schema["required"]) == {"display_criteria", "values", "excluded"}


def test_no_free_key_dict():
    # strict 모드는 additionalProperties(자유 키 dict)를 지원하지 않음
    schema = EvaluationCriteriaAnalysis.model_json_schema()
    for definition in [schema, *schema.get("$defs", {}).values()]:
        for prop in definition.get("properties", {}).values():
            assert "additionalProperties" not in prop


def test_prompt_example_output_validates():
    # SYSTEM_PROMPT 첫 번째 예시 출력
    data = {
        "display_criteria": ["추임새 5번 이하", "대본 없이 발표하기"],
        "values": {**EMPTY_VALUES, "filler": "5회 이하", "script_used": False},
        "excluded": ["발표 자료 디자인"],
    }
    result = EvaluationCriteriaAnalysis.model_validate(data)
    assert result.values.script_used is False
    assert result.values.speed is None
    assert result.model_dump() == data


def test_missing_field_rejected():
    values = {k: v for k, v in EMPTY_VALUES.items() if k != "gaze"}
    with pytest.raises(ValidationError):
        Criteria.model_validate(values)


def test_script_used_rejects_non_bool():
    with pytest.raises(ValidationError):
        Criteria.model_validate({**EMPTY_VALUES, "script_used": "대본 참고"})
