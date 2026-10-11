"""예시 파일 계약 — 문서와 구현이 같은 값을 말하는지 확인한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coach import decide
from coach.judges import Judges
from coach.schemas import CoachRequest, CoachResponse, IssueCriteria, JudgmentResult
from coach.timing import criteria, judge, summarize

EXAMPLES = Path(__file__).parents[2] / "src/coach/examples"
TIMING = EXAMPLES / "timing"
EVALUATE = EXAMPLES / "evaluate"


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


# ── evaluate 요청 · 응답 ────────────────────────────────────────────────────────


def load_evaluate(name: str) -> Any:
    return json.loads((EVALUATE / name).read_text(encoding="utf-8"))


class RecordedJudge:
    """judge_results.json 에 적힌 대로 답하는 판정 모듈. 계약 모양만 알고 기준은 모른다."""

    def __init__(self, recorded: dict[str, Any]) -> None:
        self.recorded = recorded
        #: 적어 두지 않은 입력으로 부른 summarize. 코치는 summarize 예외를 삼키므로 따로 본다
        self.misses: list[dict[str, Any]] = []
        self.summarized = 0

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[dict[str, Any]]:  # noqa: ARG002
        return self.recorded["judge"]

    def criteria(self) -> dict[str, Any]:
        return self.recorded["criteria"]

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        self.summarized += 1
        for item in self.recorded["summarize"]:
            if item["tally"] == tally:
                return item["metrics"]
        self.misses.append(tally)
        raise AssertionError(f"적어 두지 않은 summarize 입력: {tally}")


def recorded_judges() -> tuple[Judges, dict[str, RecordedJudge]]:
    results = load_evaluate("judge_results.json")
    modules = {name: RecordedJudge(results[name]) for name in ("gaze", "pace", "volume", "filler")}
    # 기준 음량은 요청의 calibration 으로 온다
    return Judges(**modules, baseline=lambda samples: None), modules  # noqa: ARG005


def test_evaluate_response_matches_example() -> None:
    judges, modules = recorded_judges()
    response = decide(load_evaluate("request.json"), judges)
    assert_same(response.model_dump(mode="json"), load_evaluate("response.json"))
    # 코치가 적어 둔 그대로의 합계로 summarize 를 불렀다 (미션의 지금 값)
    for name, module in modules.items():
        assert module.misses == [], name
        assert module.summarized == len(module.recorded["summarize"]), name


def test_evaluate_example_files_follow_the_contract() -> None:
    CoachRequest.model_validate(load_evaluate("request.json"))
    CoachResponse.model_validate(load_evaluate("response.json"))
    for name, recorded in load_evaluate("judge_results.json").items():
        assert recorded["judge"], name
        for raw in recorded["judge"]:
            assert JudgmentResult.model_validate(raw).evaluator == name
        for raw in recorded["criteria"].values():
            IssueCriteria.model_validate(raw)


def test_evaluate_example_is_an_intervention_like_the_document() -> None:
    response = load_evaluate("response.json")
    assert response["action"] == "INTERVENE"
    assert response["feedback"]["instruction"] == "LOOK_AT_CAMERA"
    assert {"GAZE_ON_SCRIPT", "MISSION_RELEVANT", "RECURRING", "PLAN_FOCUS"} <= set(
        response["reason_codes"]
    )
    versions = response["meta"]["criteria_versions"]
    assert versions.keys() == {"gaze", "pace", "volume", "filler", "timing", "coach"}
