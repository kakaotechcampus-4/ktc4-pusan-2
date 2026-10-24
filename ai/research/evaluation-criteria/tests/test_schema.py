import pytest
from pydantic import ValidationError

from evaluation_criteria.schemas import Criteria, EvaluationCriteriaAnalysis

CRITERIA_FIELDS = [
    "speed",
    "pause",
    "silence",
    "volume",
    "filler",
    "gaze",
    "script_dependency",
    "script_similarity",
    "script_used",
]
EMPTY_VALUES = dict.fromkeys(CRITERIA_FIELDS)


def test_criteria_fields_fixed():
    # 문서 5-3의 요소 표(9개)와 스키마 필드가 어긋나면 안 됨
    assert list(Criteria.model_fields) == CRITERIA_FIELDS


def test_all_fields_required():
    # json_schema strict 모드: 모든 필드가 required여야 함 (null은 값으로 허용)
    criteria_schema = Criteria.model_json_schema()
    assert set(criteria_schema["required"]) == set(CRITERIA_FIELDS)

    analysis_schema = EvaluationCriteriaAnalysis.model_json_schema()
    assert set(analysis_schema["required"]) == {"display_criteria", "values", "targets", "excluded"}


def test_no_free_key_dict():
    # strict 모드는 additionalProperties(자유 키 dict)를 지원하지 않음
    schema = EvaluationCriteriaAnalysis.model_json_schema()
    for definition in [schema, *schema.get("$defs", {}).values()]:
        for prop in definition.get("properties", {}).values():
            assert "additionalProperties" not in prop


def test_prompt_example_output_validates():
    # 문서 5-5 첫 번째 예시 응답 (meta 제외 = LLM 출력)
    data = {
        "display_criteria": ["추임새 5번 이하", "청중 자주 보기"],
        "values": {**EMPTY_VALUES, "filler": "5번 이하", "gaze": "청중을 자주 보기"},
        "targets": [
            {"display": "추임새 5번 이하", "metric": "filler_count", "operator": "LTE", "value": 5}
        ],
        "excluded": ["발표는 5분 안에 끝내기"],
    }
    result = EvaluationCriteriaAnalysis.model_validate(data)
    assert result.values.script_used is None
    assert result.values.silence is None
    assert result.targets[0].metric == "filler_count"
    assert result.model_dump() == data


def test_missing_field_rejected():
    values = {k: v for k, v in EMPTY_VALUES.items() if k != "gaze"}
    with pytest.raises(ValidationError):
        Criteria.model_validate(values)


def test_script_used_rejects_non_bool():
    with pytest.raises(ValidationError):
        Criteria.model_validate({**EMPTY_VALUES, "script_used": "대본 참고"})
