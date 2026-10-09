"""예시 파일 계약 — 문서와 구현이 같은 값을 말하는지 확인한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coach.schemas import JudgmentResult
from coach.timing import criteria, judge, summarize

TIMING = Path(__file__).parents[2] / "src/coach/examples/timing"


def load(name: str) -> Any:
    return json.loads((TIMING / name).read_text(encoding="utf-8"))


def assert_same(actual: Any, expected: Any) -> None:
    """모양은 같고 실수는 근사로 비교한다."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for k in expected:
            assert_same(actual[k], expected[k])
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            assert_same(a, e)
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9)
    else:
        assert actual == expected


def run() -> list[dict[str, Any]]:
    inputs = load("judge_input.json")
    return [r.model_dump(mode="json") for r in judge(inputs, inputs["t_ms"])]


def test_timing_judge_matches_example() -> None:
    assert_same(run(), load("judge_output.json"))


def test_timing_criteria_matches_example() -> None:
    actual = {k: v.model_dump(mode="json") for k, v in criteria().items()}
    assert_same(actual, load("criteria.json"))


def test_timing_summarize_matches_example() -> None:
    assert_same(summarize(load("summarize_input.json")), load("summarize_output.json"))


def test_timing_output_follows_common_contract() -> None:
    since = load("judge_input.json")["since_ms"]
    for raw in load("judge_output.json"):
        result = JudgmentResult.model_validate(raw)
        assert result.counted_until_ms <= result.t_ms
        assert all(i.area == result.area for i in result.issues)
        assert all(0.0 <= i.severity <= 1.0 for i in result.issues)
        assert all(p.t_ms >= since for p in result.tally)


def test_timing_judge_is_deterministic() -> None:
    assert run() == run()


def test_timing_recall_with_cursor_has_empty_tally() -> None:
    inputs = load("judge_input.json")
    first = judge(inputs, inputs["t_ms"])[0]
    again = judge({**inputs, "since_ms": first.counted_until_ms}, inputs["t_ms"])[0]
    assert again.tally == []
