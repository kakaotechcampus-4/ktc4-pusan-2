"""진입 함수 analyze_criteria (evaluation-criteria.md 4-2 · 6 · 7절). 가짜 LLM 으로 본다."""

import pytest

from evaluation_criteria import analyze_criteria
from evaluation_criteria.prompts import SYSTEM_PROMPT
from evaluation_criteria.schemas import EvaluationCriteriaRequest, EvaluationCriteriaResponse
from evaluation_criteria.version import FEATURE_VERSION, SCHEMA_VERSION
from tests.fakes import FakeLLM, analysis


def run(output=None, text="추임새는 5번 이하로 해 주세요.", llm=None):
    llm = llm or FakeLLM(output)
    return analyze_criteria(EvaluationCriteriaRequest(standard_text=text), llm), llm


def test_llm_is_called_once_with_prompt_and_stripped_text():
    _, llm = run(text="  추임새 5번 이하 \n")
    assert llm.calls == [[("system", SYSTEM_PROMPT), ("user", "추임새 5번 이하")]]


def test_returns_response_model_with_meta():
    response, _ = run()
    assert isinstance(response, EvaluationCriteriaResponse)
    assert response.meta.schema_version == SCHEMA_VERSION
    assert response.meta.feature_version == FEATURE_VERSION
    # 응답한 모델은 서비스 라우터가 채운다 (6절 3번)
    assert response.meta.model is None


def test_llm_output_is_passed_through():
    output = analysis(
        display_criteria=["추임새 5번 이하"],
        values={"filler": "5번 이하"},
        targets=[{"display": "추임새 5번 이하", "metric": "filler_count", "operator": "LTE", "value": 5}],
        excluded=["발표는 5분 안에 끝내기"],
    )
    response, _ = run(output)
    assert response.model_dump(exclude={"meta"}) == output.model_dump()


@pytest.mark.parametrize("script_used", [True, False, None])
def test_script_used_three_values(script_used):
    response, _ = run(analysis(values={"script_used": script_used}))
    assert response.values.script_used is script_used


def test_only_unmeasurable_criteria_is_not_a_failure():
    # 7절: 측정할 수 있는 기준이 하나도 없으면 표시 기준 · 목표 빈 목록, 기준은 excluded 에
    output = analysis(excluded=["발표 주제는 창의적이어야", "발표 시간은 7분"])
    response, _ = run(output, text="발표 주제는 창의적이어야 하고 발표 시간은 7분입니다")
    assert response.display_criteria == []
    assert response.targets == []
    assert response.excluded == ["발표 주제는 창의적이어야", "발표 시간은 7분"]


def test_bad_target_is_dropped_but_display_criteria_stays():
    output = analysis(
        display_criteria=["청중 70% 이상 보기"],
        values={"gaze": "70% 이상"},
        targets=[{"display": "청중 70% 이상 보기", "metric": "audience_ratio", "operator": "GTE", "value": 70}],
    )
    response, _ = run(output)
    assert response.targets == []
    assert response.display_criteria == ["청중 70% 이상 보기"]


def test_display_criteria_are_cut_to_10_and_their_targets_dropped():
    displays = [f"기준 {i}" for i in range(12)]
    targets = [
        {"display": displays[0], "metric": "filler_count", "operator": "LTE", "value": 5},
        {"display": displays[11], "metric": "pause_count", "operator": "LTE", "value": 3},
    ]
    response, _ = run(analysis(display_criteria=displays, targets=targets))
    assert response.display_criteria == displays[:10]
    assert [t.display for t in response.targets] == [displays[0]]


def test_llm_errors_are_not_caught():
    # 7절: 코어는 LLM 예외를 잡지 않는다. 서비스의 common/errors.py 가 상태 코드로 바꾼다
    with pytest.raises(TimeoutError):
        run(llm=FakeLLM(error=TimeoutError()))
