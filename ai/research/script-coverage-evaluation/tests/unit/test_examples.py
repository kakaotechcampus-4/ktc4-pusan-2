"""API 예시(`src/script_coverage/examples/`)의 계약 테스트.

`llm_cache.json` 으로 만든 캐시와, 부르면 시간 초과가 되는 LLM 으로 `request.json` 을 진입 함수에 넣어
`response.json` 과 같은 응답이 나오는지 본다. `coverage_lab` 을 쓰지 않는다 — 코어와 함께 서비스로 옮겨 가는 테스트다.
"""

import json
from pathlib import Path

import pytest

from script_coverage import core as core_module
from script_coverage.core import ScriptLLMs, analyze_script

# 예시 폴더는 코어 패키지 안에 있다. 코어를 옮기면 위 import 앞부분만 바뀐다
EXAMPLES = Path(core_module.__file__).resolve().parent / "examples"
MODEL = "openai/gpt-5.6-luna"  # 예시를 만든 모델. 캐시 항목의 설정 해시가 이 이름에 의존한다


class TimeoutLLM:
    def invoke(self, messages):
        raise TimeoutError


class DictCache:
    """`llm_cache.json` 의 항목으로 만든 읽기 전용 캐시."""

    def __init__(self, entries: list[dict]):
        self.rows = {
            (e["kind"], e["key"], e["config_hash"]): json.dumps(e["output"], ensure_ascii=False)
            for e in entries
        }

    def get(self, kind, key, config_hash):
        return self.rows.get((kind, key, config_hash))

    def put(self, kind, key, config_hash, output_json):
        pass


def _load(api: str, name: str):
    return json.loads((EXAMPLES / api / name).read_text(encoding="utf-8"))


def assert_same(actual, expected, path="$"):
    """JSON 값을 재귀로 비교한다. 실수만 약간의 오차를 허용한다."""
    if isinstance(expected, float) or isinstance(actual, float):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), path
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key in expected:
            assert_same(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_same(a, e, f"{path}[{i}]")
    else:
        assert actual == expected, path


def test_rubric_example():
    llm = TimeoutLLM()
    response = analyze_script(
        _load("rubric", "request.json"),
        llms=ScriptLLMs(semantic=llm, final=llm, unit=llm),
        model=MODEL,
        cache=DictCache(_load("rubric", "llm_cache.json")),
    )
    expected = _load("rubric", "response.json")
    assert [r["meta"]["slide_number"] for r in expected["rubrics"]] == [6]
    assert [f["slide_number"] for f in expected["failed"]] == [7]
    assert_same(response.model_dump(mode="json"), expected)


def test_assert_same_detects_differences():
    assert_same({"a": [1, 0.1 + 0.2]}, {"a": [1, 0.3]})
    with pytest.raises(AssertionError):
        assert_same({"a": [1, 2]}, {"a": [1, 3]})
    with pytest.raises(AssertionError):
        assert_same({"a": 1}, {"a": 1, "b": 2})
