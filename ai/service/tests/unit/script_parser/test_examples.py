"""예시 파일 계약 — examples/의 요청 · 응답을 BE와 다른 모듈이 그대로 믿어도 되는지 확인한다."""

import json
from pathlib import Path

import pytest

from pitch_coach_ai.features.script_parser import core, parse_script
from pitch_coach_ai.features.script_parser.schemas import LLMOutput, ParseRequest, ParseResponse

# 예시는 코어 패키지 안에 있다 — 패키지 위치가 바뀌어도 경로를 고치지 않게 모듈 파일 기준으로 찾는다
EXAMPLES = Path(core.__file__).resolve().parent / "examples"
CASES = sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir())


class FakeLLM:
    def __init__(self, output: dict):
        self.output = output

    def invoke(self, messages):
        return LLMOutput.model_validate(self.output)


def load(case: str, name: str) -> dict:
    return json.loads((EXAMPLES / case / name).read_text(encoding="utf-8"))


def test_cases_exist():
    assert CASES == ["fail", "no_target", "success"]


@pytest.mark.parametrize("case", CASES)
def test_response_matches_example(case):
    request = ParseRequest.model_validate(load(case, "request.json"))
    response = parse_script(request, FakeLLM(load(case, "llm_output.json")))
    assert response.model_dump(mode="json") == load(case, "response.json")


@pytest.mark.parametrize("case", CASES)
def test_example_follows_contract(case):
    request = ParseRequest.model_validate(load(case, "request.json"))
    response = ParseResponse.model_validate(load(case, "response.json"))
    numbers = [s.slide_number for s in response.slides]
    assert numbers == sorted(set(numbers))
    if response.status == "fail":
        assert numbers == [1]
    if request.target_ms is not None:
        assert sum(s.expected_ms for s in response.slides) == request.target_ms
    for s in response.slides:
        assert [s.script[h.start:h.end] for h in s.highlights] == s.keywords
