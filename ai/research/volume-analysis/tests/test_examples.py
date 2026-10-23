"""예시 파일 계약 — examples/ 의 입력 · 출력이 구현과 같은지, 계약 모델에 맞는지 본다.

다른 모듈 · BE 는 이 예시로 먼저 개발한다.
기준값을 바꾸면 criteria_version 이 바뀌므로 예시도 다시 만든다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from volume.core import baseline, criteria, judge, speaking_levels, summarize
from volume.schemas import Criteria, PauseJudgment, Summary, VolumeInput, VolumeJudgment

EXAMPLES = Path(__file__).parents[1] / "src/volume/examples"


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


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
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9)
    else:
        assert actual == expected


def test_judge_matches_example() -> None:
    inputs = load("judge_input.json")
    actual = [r.model_dump(mode="json") for r in judge(inputs, inputs["t_ms"])]
    assert_same(actual, load("judge_output.json"))


def test_summarize_matches_example() -> None:
    assert_same(
        summarize(load("summarize_input.json")).model_dump(mode="json"),
        load("summarize_output.json"),
    )


def test_criteria_matches_example() -> None:
    assert_same(criteria().model_dump(mode="json"), load("criteria.json"))


def test_speaking_levels_matches_example() -> None:
    actual = speaking_levels(load("speaking_levels_input.json")["records"])
    assert_same(actual, load("speaking_levels_output.json"))


def test_baseline_matches_example() -> None:
    assert_same(baseline(load("baseline_input.json")["levels"]), load("baseline_output.json"))


def test_examples_follow_contract() -> None:
    inputs = load("judge_input.json")
    VolumeInput.model_validate(inputs)
    volume, pause = load("judge_output.json")
    for model, raw in ((VolumeJudgment, volume), (PauseJudgment, pause)):
        result = model.model_validate(raw)
        assert result.counted_until_ms == max(result.t_ms, inputs["since_ms"])
        assert all(i.area == result.area for i in result.issues)
        assert all(p.t_ms >= inputs["since_ms"] for p in result.tally)
    Summary.model_validate(load("summarize_output.json"))
    Criteria.model_validate(load("criteria.json"))
