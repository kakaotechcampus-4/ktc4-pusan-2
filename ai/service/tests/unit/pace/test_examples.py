"""예시 파일 계약 — examples/ 가 지금 코드의 입력 · 출력과 같고 문서가 말하는 것을 보여 주는지 본다.

기준값(config.py)을 바꾸면 출력이 달라져 이 테스트가 깨진다. 그때는 예시 출력을 다시 만들어
같은 PR 에 넣는다(architecture.md 12절).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pitch_coach_ai.features.pace import core as pace_core
from pitch_coach_ai.features.pace.core import criteria, judge, summarize
from pitch_coach_ai.features.pace.schemas import IssueCriteria, PaceInput, TallyItem

from .helpers import check_contract, time_items, word_items

# 예시는 코어 패키지 안에 있다 — 패키지 위치가 바뀌어도 경로를 고치지 않게 모듈 파일 기준으로 찾는다
EXAMPLES = Path(pace_core.__file__).resolve().parent / "examples"
#: judge 예시: 기본(pace.md 5-5) · 경계 — 보류 단어 · 경계 — 군더더기 판정 실패
CASES = ["", "_pending", "_fillers_null"]
IDS = ["basic", "pending", "fillers_null"]


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
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9)
    else:
        assert actual == expected


def run(case: str) -> list[dict[str, Any]]:
    inputs = load(f"judge_input{case}.json")
    return [r.model_dump(mode="json") for r in judge(inputs, inputs["t_ms"])]


def test_examples_folder_has_every_file():
    expected = {f"judge_{kind}{case}.json" for case in CASES for kind in ("input", "output")}
    expected |= {"criteria.json", "summarize_input.json", "summarize_output.json"}
    assert {p.name for p in EXAMPLES.glob("*.json")} == expected


# ── 지금 코드의 출력과 같음 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_judge_matches_example(case: str):
    assert_same(run(case), load(f"judge_output{case}.json"))


def test_criteria_matches_example():
    actual = {k: v.model_dump(mode="json") for k, v in criteria().items()}
    assert_same(actual, load("criteria.json"))


def test_summarize_matches_example():
    assert_same(summarize(load("summarize_input.json")), load("summarize_output.json"))


# ── 공통 계약 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_example_files_follow_the_contract(case: str):
    inputs = load(f"judge_input{case}.json")
    PaceInput.model_validate(inputs)
    # BE 가 보내는 창: 최근 60초 안에 확정된 단어
    assert all(inputs["t_ms"] - 60_000 < w["end_ms"] <= inputs["t_ms"] for w in inputs["words"])
    [result] = judge(inputs, inputs["t_ms"])
    check_contract(result, inputs)
    for raw in load("criteria.json").values():
        IssueCriteria.model_validate(raw)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_judging_an_example_twice_gives_the_same_result(case: str):
    assert run(case) == run(case)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_recalling_an_example_with_the_returned_cursors_counts_nothing_new(case: str):
    inputs = load(f"judge_input{case}.json")
    [first] = judge(inputs, inputs["t_ms"])
    again = {
        **inputs,
        "since_ms": first.counted_until_ms,
        "words_since_ms": first.words_counted_until_ms,
    }
    assert judge(again, inputs["t_ms"])[0].tally == []


# ── 문서(pace.md 5-5)가 말하는 것을 보여 줌 ─────────────────────────────────────


def test_judge_examples_are_one_take_judged_at_41_and_52_seconds():
    basic, pending, null = (load(f"judge_input{c}.json") for c in CASES)
    assert pending["words"] == null["words"]
    assert basic["words"] == [w for w in pending["words"] if w["end_ms"] <= basic["t_ms"]]
    # 같은 군더더기 "음" 을 두 요청 모두 군더더기로 넘긴다
    assert basic["fillers"][0] in pending["fillers"]


def test_basic_example_counts_the_two_new_words_of_the_document():
    inputs = load("judge_input.json")
    assert {k: inputs[k] for k in ("t_ms", "since_ms", "words_since_ms")} == {
        "t_ms": 41000,
        "since_ms": 40000,
        "words_since_ms": 39300,
    }
    assert inputs["words"][-4:] == [
        {"word": "지난", "start_ms": 38200, "end_ms": 38450},
        {"word": "음", "start_ms": 38900, "end_ms": 39300},
        {"word": "분기", "start_ms": 39700, "end_ms": 40050},
        {"word": "매출은", "start_ms": 40100, "end_ms": 40600},
    ]
    [result] = judge(inputs, inputs["t_ms"])
    assert result.measurable and result.state == "NORMAL" and result.issues == []
    # "지난" · "음" 은 커서 앞이라 다시 세지 않고, "분기" 앞 간격 400 ms 는 멈춤이 아니다
    assert word_items(result) == [
        TallyItem(t_ms=39700, values={"chars": 2, "speak_ms": 350}),
        TallyItem(t_ms=40100, values={"chars": 3, "speak_ms": 500}),
    ]
    assert time_items(result) == [TallyItem(t_ms=40000, values={"ok_ms": 1000, "total_ms": 1000})]
    assert result.words_counted_until_ms == 40600


def test_pending_example_stops_the_word_cursor_before_the_pending_word():
    inputs = load("judge_input_pending.json")
    [pending] = [f for f in inputs["fillers"] if f["is_filler"] is None]
    before = max(w["end_ms"] for w in inputs["words"] if w["end_ms"] <= pending["start_ms"])
    [result] = judge(inputs, inputs["t_ms"])
    assert result.words_counted_until_ms == before
    assert pending["start_ms"] not in {i.t_ms for i in word_items(result)}
    # 빨라진 말: 문제가 실린 결과의 모양도 보여 준다
    assert result.state == "FAST"
    assert result.issues[0].evidence == {"cpm": result.metrics.cpm, "window_ms": 15_000}


def test_fillers_null_example_keeps_the_word_cursor_and_counts_no_ok_time():
    inputs = load("judge_input_fillers_null.json")
    [result] = judge(inputs, inputs["t_ms"])
    assert result.state == "UNKNOWN" and not result.measurable
    assert result.words_counted_until_ms == inputs["words_since_ms"]
    assert result.tally == [TallyItem(t_ms=inputs["since_ms"], values={"total_ms": 1000})]


def test_summarize_example_is_the_take_summary_of_the_document():
    assert load("summarize_output.json") == {
        "SPEED": {"cpm": 312.4, "pause_count": 6, "measured_ratio": 0.97}
    }
