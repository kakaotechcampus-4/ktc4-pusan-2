"""최상위 진입 함수 `core.analyze_script` (`/scripts/rubric`): 요청 검증 · 실패 모양 · 오류 코드."""

import pytest
from pydantic import ValidationError

from script_coverage.core import STATUS_CODES, CoverageError, ScriptLLMs, analyze_script
from script_coverage.schemas import RubricRequest
from script_coverage.shared.llm_step import ERROR_MESSAGES
from script_coverage.version import FEATURE_VERSION, SCHEMA_VERSION
from tests.unit.fakes import FakeLLM, empty_script_llms
from tests.unit.shared.slides import SLIDES


class CodedError(Exception):
    """서비스의 LlmError 하위 예외처럼 `code` 속성을 가진 예외."""

    code = "LLM_UNAVAILABLE"


def _request(numbers=(4, 6, 7), slide_numbers=None, scripts=None) -> dict:
    return {
        "pitch_id": "p-1",
        "script_version_id": "sv-3",
        "slides": [
            {"slide_number": n, "script": (scripts or {}).get(n, SLIDES.get(("가상대본1", n), ""))}
            for n in numbers
        ],
        "slide_numbers": slide_numbers,
    }


def _llms(**kwargs) -> ScriptLLMs:
    semantic, final, unit = empty_script_llms(**kwargs)
    return ScriptLLMs(semantic=semantic, final=final, unit=unit)


def _analyze(request, llms=None):
    return analyze_script(request, llms=llms or _llms(), model="test-model")


def test_slide_numbers_pick_slides_and_meta():
    llms = _llms()
    response = _analyze(_request(slide_numbers=[7, 4]), llms)
    assert [r.meta.slide_number for r in response.rubrics] == [4, 7]
    assert response.failed == []
    assert response.meta.model_dump() == {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model": "test-model",
    }
    assert llms.semantic.calls == 2  # 6번 장은 부르지 않는다
    assert {r.meta.script_name for r in response.rubrics} == {"sv-3"}


def test_no_slide_numbers_means_every_slide():
    assert [r.meta.slide_number for r in _analyze(_request()).rubrics] == [4, 6, 7]


def test_empty_slide_numbers_is_an_empty_success():
    llms = _llms()
    response = _analyze(_request(slide_numbers=[]), llms)
    assert response.rubrics == [] and response.failed == []
    assert llms.semantic.calls == 0


def test_same_input_same_output():
    first = _analyze(_request()).model_dump(mode="json")
    second = _analyze(_request()).model_dump(mode="json")
    assert first == second


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (CodedError("속성"), "LLM_UNAVAILABLE"),
        (TimeoutError("대본 원문"), "LLM_TIMEOUT"),
        (ValidationError.from_exception_data("Out", []), "LLM_INVALID_OUTPUT"),
        (RuntimeError("대본 원문"), "LLM_ERROR"),
    ],
)
def test_failed_slide_shape_and_code_mapping(exc, code):
    """한 장만 실패하면 200 이다: 성공한 장은 rubrics, 실패한 장은 failed (고정 문구)."""
    # 6번 장의 1차 분석에만 예외를 낸다 (다른 장은 캐시 없이 빈 응답)
    semantic = FakeLLM(None)

    def invoke(messages):
        semantic.calls += 1
        if "[슬라이드 6]" in messages[1][1]:
            raise exc
        return empty_script_llms()[0].result

    semantic.invoke = invoke
    llms = ScriptLLMs(semantic=semantic, final=empty_script_llms()[1], unit=empty_script_llms()[2])
    response = _analyze(_request(slide_numbers=[4, 6]), llms)
    assert [r.meta.slide_number for r in response.rubrics] == [4]
    [failure] = response.failed
    assert failure.slide_number == 6
    assert failure.error.code == code
    assert failure.error.message == ERROR_MESSAGES[code]
    assert "원문" not in failure.error.message and "속성" not in failure.error.message


def test_all_target_slides_failed_raises_with_lowest_slide_llm_code():
    """모두 실패하면 요청 전체 오류. 장 번호가 가장 작은 장의 코드를 쓴다."""

    class ByNumber:
        def invoke(self, messages):
            raise TimeoutError if "[슬라이드 4]" in messages[1][1] else RuntimeError

    semantic = ByNumber()
    _, final, unit = empty_script_llms()
    with pytest.raises(CoverageError) as caught:
        _analyze(
            _request(slide_numbers=[6, 4]), ScriptLLMs(semantic=semantic, final=final, unit=unit)
        )
    error = caught.value
    assert (error.status_code, error.code) == (504, "LLM_TIMEOUT")
    assert error.message == ERROR_MESSAGES["LLM_TIMEOUT"]


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (RuntimeError("x"), 502, "LLM_ERROR"),
        (ValidationError.from_exception_data("O", []), 502, "LLM_INVALID_OUTPUT"),
    ],
)
def test_all_failed_status_by_code(exc, status, code):
    llms = _llms(semantic=exc)
    with pytest.raises(CoverageError) as caught:
        _analyze(_request(numbers=(6,)), llms)
    assert (caught.value.status_code, caught.value.code) == (status, code)
    assert STATUS_CODES[code] == status


def test_llms_none_is_503_unavailable():
    with pytest.raises(CoverageError) as caught:
        analyze_script(_request(), llms=None, model="test-model")
    error = caught.value
    assert (error.status_code, error.code) == (503, "LLM_UNAVAILABLE")
    assert error.message == ERROR_MESSAGES["LLM_UNAVAILABLE"]


def test_accepts_request_object_and_ignores_unknown_fields():
    request = RubricRequest.model_validate({**_request(numbers=(6,)), "extra": 1})
    assert [r.meta.slide_number for r in _analyze(request).rubrics] == [6]


def test_request_validation():
    with pytest.raises(ValidationError):  # 같은 장이 둘
        RubricRequest.model_validate(_request(numbers=(6, 6)))
    with pytest.raises(ValidationError):  # slides 에 없는 slide_numbers
        RubricRequest.model_validate(_request(numbers=(6,), slide_numbers=[6, 9]))
    with pytest.raises(ValidationError):  # slides 가 빔
        RubricRequest.model_validate(_request(numbers=()))
    with pytest.raises(ValidationError):  # 장 번호는 1 이상
        RubricRequest.model_validate(_request(numbers=(0,)))
    with pytest.raises(ValidationError):  # 필수 필드
        RubricRequest.model_validate({"slides": [{"slide_number": 1, "script": ""}]})


def test_empty_script_slide_is_accepted_without_llm():
    llms = _llms()
    response = _analyze(_request(numbers=(1, 6), scripts={1: ""}, slide_numbers=[1]), llms)
    assert [r.meta.slide_number for r in response.rubrics] == [1]
    assert llms.semantic.calls == 0
