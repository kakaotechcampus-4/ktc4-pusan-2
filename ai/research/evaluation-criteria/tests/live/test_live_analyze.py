"""실제 LLM API 로 예시 문장을 분석한다 (과금, examples/ 요청 수만큼 호출).

가짜 LLM 테스트는 검사 · 계약을 본다. 이 테스트는 구조화 출력 LLM 을 만들고 부르는 경로가
실제로 동작하고, 출력이 스키마를 통과하는지 확인한다 (evaluation-criteria.md 10절).
기본 실행에서 빠진다 — `uv run python -m pytest -m live` 로 따로 돌린다.
"""

import json
from pathlib import Path

import pytest

from evaluation_criteria import analyze_criteria
from evaluation_criteria.schemas import EvaluationCriteriaRequest, EvaluationCriteriaResponse
from run import build_llm

pytestmark = pytest.mark.live

EXAMPLES = Path(__file__).resolve().parents[2] / "src" / "evaluation_criteria" / "examples"
REQUESTS = sorted(EXAMPLES.glob("request*.json"))


@pytest.fixture(scope="module")
def llm():
    return build_llm()


@pytest.mark.parametrize("path", REQUESTS, ids=lambda p: p.stem)
def test_real_llm_output_passes_the_schema(llm, path):
    request = EvaluationCriteriaRequest.model_validate_json(path.read_text(encoding="utf-8"))
    response = analyze_criteria(request, llm)
    EvaluationCriteriaResponse.model_validate(response.model_dump())
    displays = set(response.display_criteria)
    assert all(t.display in displays for t in response.targets)
    print(json.dumps(response.model_dump(), ensure_ascii=False))
