"""실제 LLM으로 예시 대본을 돌린다 (과금). `uv run python -m pytest -m live`로만 돈다."""

import json
from pathlib import Path

import pytest

from run import build_llm
from script_parser import parse_script
from script_parser.schemas import ParseRequest

EXAMPLES = Path(__file__).parents[2] / "src/script_parser/examples"

pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", ["success", "fail"])
def test_example_with_real_llm(case):
    request = ParseRequest.model_validate(
        json.loads((EXAMPLES / case / "request.json").read_text(encoding="utf-8"))
    )
    expected = json.loads((EXAMPLES / case / "response.json").read_text(encoding="utf-8"))

    # 추론 강도는 평가에서 고른 값(README 평가 결과). 기본값이면 추론이 출력 한도를 다 써 실패할 수 있다
    response = parse_script(request, build_llm(reasoning_effort="low"))

    # 장 나누기 · 판정은 코드가 하므로 예시와 같아야 한다. 키워드 · 용어는 LLM마다 다를 수 있다
    assert response.status == expected["status"]
    assert [s.slide_number for s in response.slides] == [s["slide_number"] for s in expected["slides"]]
    assert [s.script for s in response.slides] == [s["script"] for s in expected["slides"]]
    assert sum(s.expected_ms for s in response.slides) == request.target_ms
    for s in response.slides:
        assert all(k in s.script for k in s.keywords)
        assert [s.script[h.start:h.end] for h in s.highlights] == s.keywords
