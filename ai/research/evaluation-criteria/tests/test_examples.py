"""예시 파일 계약 — examples/ 가 지금 코드의 입력 · 출력과 같은지 본다 (evaluation-criteria.md 5-5).

LLM 출력 부분을 가짜 LLM 이 돌려주고, analyze_criteria 가 예시 응답을 그대로 내는지 확인한다.
meta.model 은 서비스 라우터가 채우므로 비교하지 않는다.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from evaluation_criteria import analyze_criteria
from evaluation_criteria.schemas import (
    EvaluationCriteriaAnalysis,
    EvaluationCriteriaRequest,
    EvaluationCriteriaResponse,
)
from tests.fakes import FakeLLM

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "evaluation_criteria" / "examples"
#: 기본(5-5) · 경계 — 긴 침묵 · 말 속도 · 대본 사용 · 경계 — 숫자 목표 없음
CASES = ["", "_long_silence", "_no_targets"]
IDS = ["basic", "long_silence", "no_targets"]


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_examples_folder_has_every_file():
    expected = {f"{kind}{case}.json" for case in CASES for kind in ("request", "response")}
    assert {p.name for p in EXAMPLES.glob("*.json")} == expected


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_examples_match_the_contract(case):
    EvaluationCriteriaRequest.model_validate(load(f"request{case}.json"))
    EvaluationCriteriaResponse.model_validate(load(f"response{case}.json"))


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_core_returns_the_example_response(case):
    request = EvaluationCriteriaRequest.model_validate(load(f"request{case}.json"))
    expected = load(f"response{case}.json")
    llm_output = EvaluationCriteriaAnalysis.model_validate(
        {k: v for k, v in expected.items() if k != "meta"}
    )
    actual = analyze_criteria(request, FakeLLM(llm_output)).model_dump(mode="json")
    assert actual["meta"].pop("model") is None
    expected["meta"].pop("model")
    assert actual == expected


def test_response_example_fields_follow_the_doc_order():
    # 5-5 의 필드 순서 그대로 (BE · FE 가 예시를 읽기 쉽게)
    response = load("response.json")
    assert list(response) == ["display_criteria", "values", "targets", "excluded", "meta"]
