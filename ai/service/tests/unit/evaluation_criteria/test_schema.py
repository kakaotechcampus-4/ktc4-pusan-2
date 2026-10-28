import pytest
from pydantic import ValidationError

from pitch_coach_ai.features.evaluation_criteria.schemas import (
    Criteria,
    EvaluationCriteriaAnalysis,
    EvaluationCriteriaRequest,
    Target,
)

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


# ── 표시 기준 50자: 자르지 않고 출력 스키마 위반 (6절 2번) ─────────────────────


def _analysis(display: str) -> dict:
    return {"display_criteria": [display], "values": EMPTY_VALUES, "targets": [], "excluded": []}


def test_display_criterion_of_50_chars_is_allowed():
    result = EvaluationCriteriaAnalysis.model_validate(_analysis("가" * 50))
    assert result.display_criteria == ["가" * 50]


def test_display_criterion_over_50_chars_is_a_schema_violation():
    with pytest.raises(ValidationError):
        EvaluationCriteriaAnalysis.model_validate(_analysis("가" * 51))


def test_display_limit_is_not_sent_in_the_json_schema():
    # strict 모드가 받지 않는 키워드를 LLM 스키마에 싣지 않는다. 응답을 검증할 때만 본다
    schema = EvaluationCriteriaAnalysis.model_json_schema()
    assert schema["properties"]["display_criteria"]["items"] == {"type": "string"}


@pytest.mark.parametrize(
    "field, bad", [("metric", "speed_score"), ("operator", "EQ")], ids=["metric", "operator"]
)
def test_target_rejects_unknown_metric_or_operator(field, bad):
    target = {"display": "추임새 5번 이하", "metric": "filler_count", "operator": "LTE", "value": 5}
    with pytest.raises(ValidationError):
        Target.model_validate({**target, field: bad})


# ── 요청 (5-2): 앞뒤 공백을 뺀 길이 1~2,000자. 넘으면 422 이고 LLM 을 부르지 않는다 ──


@pytest.mark.parametrize(
    "text", ["", "   \n\t ", "　　", "가" * 2001], ids=["empty", "spaces", "fullwidth", "2001"]
)
def test_request_rejects_empty_or_too_long_text(text):
    with pytest.raises(ValidationError):
        EvaluationCriteriaRequest(standard_text=text)


def test_request_strips_and_allows_2000_chars():
    request = EvaluationCriteriaRequest(standard_text="  " + "가" * 2000 + "\n")
    assert request.standard_text == "가" * 2000


def test_request_ignores_unknown_fields():
    request = EvaluationCriteriaRequest.model_validate({"standard_text": "추임새 5번", "note": "x"})
    assert request.model_dump() == {"standard_text": "추임새 5번"}
