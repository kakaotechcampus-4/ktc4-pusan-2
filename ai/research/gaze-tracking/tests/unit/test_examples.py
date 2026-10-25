"""계약 예시 — examples/ 의 입력 · 출력이 지금 코드와 같고, 공통 계약과 기능 문서 5-5 에 맞는지 본다.

예시 출력은 `tools/make_examples.py` 가 만든다. 기준값 · 판정 규칙을 바꾸면 그 도구로 다시 만든다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from gaze import core
from gaze.core import criteria, judge, summarize
from gaze.schemas import GazeMetrics, IssueCriteria, JudgmentResult

# 모듈 파일 기준이라 서비스(features/gaze/)로 옮겨도 import 접두어만 바꾸면 된다
EXAMPLES = Path(core.__file__).parent / "examples"
JUDGE_INPUTS = sorted(EXAMPLES.glob("judge_input*.json"))
TALLY_KEYS = {
    "camera_ms", "screen_ms", "script_ms", "away_ms", "uncertain_ms", "unmeasured_ms",
    "measured_ms", "total_ms", "speaking_ms", "reading_ms", "reading_runs",
}  # fmt: skip


def _load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _output(path: Path) -> JudgmentResult:
    [result] = _load(path.name.replace("_input", "_output"))
    return JudgmentResult.model_validate(result)


def _by_name(stem: str) -> JudgmentResult:
    return _output(EXAMPLES / f"judge_input{stem}.json")


def check_contract(result: JudgmentResult, inputs: dict[str, Any]) -> None:
    """architecture.md 7절의 공통 결과 형식 · 커서 규칙과 기능 문서 5-3 · 5-4."""
    t, since = inputs["t_ms"], inputs["since_ms"]
    assert (result.evaluator, result.area, result.t_ms) == ("gaze", "GAZE", t)
    assert result.counted_until_ms == max(t, since)
    assert re.fullmatch(r"gaze-\d+\.\d+\+[0-9a-f]{12}", result.criteria_version)
    times = [i.t_ms for i in result.tally]
    assert times == sorted(set(times)) and all(since <= x < t for x in times)
    for item in result.tally:
        assert set(item.values) <= TALLY_KEYS and all(v >= 0 for v in item.values.values())
    rules = criteria()
    for issue in result.issues:
        rule = rules[issue.issue_type]
        assert issue.area == "GAZE" and 0 <= issue.severity <= 1 and 0 <= issue.confidence <= 1
        assert (issue.threshold, issue.bad) == (rule.threshold, rule.bad)
        assert set(issue.evidence) <= set(GazeMetrics.model_fields)
        assert issue.actionable is (issue.issue_type != "GAZE_UNMEASURABLE")
    if not result.measurable:
        assert result.state == "UNMEASURABLE"
        assert [i.issue_type for i in result.issues] == ["GAZE_UNMEASURABLE"]


def test_there_is_an_example_for_each_situation():
    assert {p.name for p in JUDGE_INPUTS} == {
        "judge_input.json",
        "judge_input_script.json",
        "judge_input_reading_run_end.json",
        "judge_input_away.json",
        "judge_input_screen.json",
        "judge_input_camera_lost.json",
        "judge_input_take_start.json",
        "judge_input_missed_requests.json",
    }


def test_examples_cover_all_five_problems():
    seen = {i.issue_type for p in JUDGE_INPUTS for i in _output(p).issues}
    assert seen == set(criteria())


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_judge_output_file_is_what_the_code_gives_now(path):
    inputs = _load(path.name)
    results = judge(inputs, inputs["t_ms"])
    assert [r.model_dump(mode="json") for r in results] == _load(
        path.name.replace("_input", "_output")
    )


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_judge_example_outputs_follow_the_common_contract(path):
    check_contract(_output(path), _load(path.name))


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_example_inputs_hold_the_whole_judgment_window(path):
    # 기록이 최근 창(30초)의 처음부터 있고, 시선 기록이 있는 1초에는 발화 시간도 있다
    inputs = _load(path.name)
    times = {r["t_ms"] for r in inputs["records"]}
    assert min(times) == max(0, inputs["t_ms"] - 30_000)
    assert times <= {v["t_ms"] for v in inputs["voiced"]}


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_calling_again_with_the_returned_cursor_counts_nothing_new(path):
    inputs = _load(path.name)
    [first] = judge(inputs, inputs["t_ms"])
    [again] = judge({**inputs, "since_ms": first.counted_until_ms}, inputs["t_ms"])
    assert again.tally == [] and again.model_dump(exclude={"tally"}) == first.model_dump(
        exclude={"tally"}
    )


# ── 예시가 보여 주는 상황 (기능 문서 5-5) ──────────────────────────────────────


def test_plain_example_has_no_problem():
    r = _by_name("")
    assert (r.measurable, r.state, r.issues) == (True, "AUDIENCE", [])


def test_script_example_matches_the_doc_example():
    r = _by_name("_script")
    assert r.state == "SCRIPT"
    [issue] = r.issues
    assert (issue.issue_type, issue.severity, issue.persistence_sec) == (
        "GAZE_ON_SCRIPT",
        0.5714,
        4.0,
    )
    assert issue.evidence == {"script_run_ms": 4000, "script_ratio_short": 0.8}
    assert [(i.t_ms, i.values) for i in r.tally] == [
        (40_000, {"script_ms": 1000, "measured_ms": 1000, "total_ms": 1000, "speaking_ms": 1000,
                  "reading_ms": 1000})
    ]  # fmt: skip


def test_reading_run_end_example_counts_one_run():
    r = _by_name("_reading_run_end")
    assert r.issues == []
    assert r.tally[0].values["reading_runs"] == 1


def test_away_example_is_one_run_across_directions():
    [issue] = _by_name("_away").issues
    assert (issue.issue_type, issue.evidence["away_run_ms"]) == ("GAZE_AWAY", 3000)


def test_screen_example_has_screen_run_and_low_eye_contact():
    r = _by_name("_screen")
    assert [i.issue_type for i in r.issues] == ["GAZE_ON_SCREEN", "GAZE_LOW_EYE_CONTACT"]


def test_camera_lost_example_matches_the_doc_example():
    r = _by_name("_camera_lost")
    assert (r.measurable, r.state) == (False, "UNMEASURABLE")
    m = r.metrics
    assert (m.measured_ratio, m.mean_reliability, m.audience_ratio_short, m.script_run_ms) == (
        0.0,
        0.0,
        None,
        0,
    )
    [issue] = r.issues
    assert (issue.severity, issue.confidence, issue.persistence_sec) == (1.0, 1.0, 5.0)
    assert issue.evidence == {"measured_ratio": 0.0, "mean_reliability": 0.0}
    assert [(i.t_ms, i.values["unmeasured_ms"]) for i in r.tally] == [(29_000, 1000)]


def test_missed_requests_example_counts_every_new_second():
    inputs = _load("judge_input_missed_requests.json")
    r = _by_name("_missed_requests")
    assert [i.t_ms for i in r.tally] == list(range(inputs["since_ms"], inputs["t_ms"], 1000))
    assert [i.values.get("unmeasured_ms", 0) for i in r.tally] == [1000, 1000, 0, 0, 0]


def test_take_start_example_has_no_problem_yet():
    r = _by_name("_take_start")
    assert r.metrics.script_run_ms >= criteria()["GAZE_ON_SCRIPT"].threshold
    assert r.issues == []


# ── summarize · criteria ──────────────────────────────────────────────────────


SUMMARIZE_INPUTS = sorted(EXAMPLES.glob("summarize_input*.json"))


@pytest.mark.parametrize("path", SUMMARIZE_INPUTS, ids=lambda p: p.name)
def test_summarize_output_file_is_what_the_code_gives_now(path):
    assert summarize(_load(path.name)) == _load(path.name.replace("_input", "_output"))


def test_summarize_examples_show_zero_denominators():
    unmeasured = _load("summarize_output_unmeasured.json")["GAZE"]
    assert (unmeasured["audience_ratio"], unmeasured["measured_ratio"]) == (None, 0.0)
    silent = _load("summarize_output_silent.json")["GAZE"]
    assert silent["reading_ratio"] is None and silent["audience_ratio"] is not None


def test_summarize_example_matches_the_doc_example_rounded():
    doc = {"audience_ratio": 0.578, "script_ratio": 0.311, "screen_ratio": 0.067,
           "away_ratio": 0.044, "reading_ratio": 0.214, "reading_runs": 4, "measured_ratio": 0.938}  # fmt: skip
    got = _load("summarize_output.json")["GAZE"]
    assert set(got) == set(doc)
    assert all(got[k] == pytest.approx(v, abs=5e-4) for k, v in doc.items())


def test_criteria_file_is_what_the_code_gives_now():
    assert _load("criteria.json") == {k: v.model_dump(mode="json") for k, v in criteria().items()}


def test_criteria_file_has_the_five_problems_with_their_metrics():
    rules = {k: IssueCriteria.model_validate(v) for k, v in _load("criteria.json").items()}
    assert {k: (v.metric, v.direction) for k, v in rules.items()} == {
        "GAZE_ON_SCRIPT": ("script_run_ms", "HIGHER_IS_WORSE"),
        "GAZE_ON_SCREEN": ("screen_run_ms", "HIGHER_IS_WORSE"),
        "GAZE_AWAY": ("away_run_ms", "HIGHER_IS_WORSE"),
        "GAZE_LOW_EYE_CONTACT": ("audience_ratio", "LOWER_IS_WORSE"),
        "GAZE_UNMEASURABLE": ("measured_ratio", "LOWER_IS_WORSE"),
    }
