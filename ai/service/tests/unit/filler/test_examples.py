"""계약 예시 — examples/ 의 입력 · 출력이 지금 코드와 같고 기능 문서 5-5 와 맞는지 본다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pitch_coach_ai.features.filler import core as filler_core
from pitch_coach_ai.features.filler.config import ALL_TIERS, DEFAULT, FillerConfig
from pitch_coach_ai.features.filler.core import criteria, criteria_version, judge, summarize
from pitch_coach_ai.features.filler.schemas import IssueCriteria, JudgmentResult

from .helpers import check_contract, word_items

# 예시는 코어 패키지 안에 있다 — 패키지 위치가 바뀌어도 경로를 고치지 않게 모듈 파일 기준으로 찾는다
EXAMPLES = Path(filler_core.__file__).resolve().parent / "examples"
JUDGE_INPUTS = sorted(EXAMPLES.glob("judge_input*.json"))


def _load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _output_name(input_name: str) -> str:
    return input_name.replace("_input", "_output")


def _config(input_name: str) -> FillerConfig:
    # tools/make_examples.py 와 같은 규칙: 이름에 default 가 들어간 예시만 배포 기본값(T1만 셈)
    return DEFAULT if "default" in input_name else ALL_TIERS


def _result(input_name: str) -> JudgmentResult:
    inputs = _load(input_name)
    [result] = judge(inputs, inputs["t_ms"], _config(input_name))
    return result


def test_there_is_an_example_for_each_situation():
    assert {p.name for p in JUDGE_INPUTS} == {
        "judge_input.json",
        "judge_input_pending.json",
        "judge_input_take_end.json",
        "judge_input_stt_down.json",
        "judge_input_frequent.json",
        "judge_input_default.json",
    }


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_judge_output_file_is_what_the_code_gives_now(path):
    inputs = _load(path.name)
    results = judge(inputs, inputs["t_ms"], _config(path.name))
    assert [r.model_dump(mode="json") for r in results] == _load(_output_name(path.name))


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_judge_example_outputs_follow_the_common_contract(path):
    result = JudgmentResult.model_validate(_load(_output_name(path.name))[0])
    check_contract(result, _load(path.name))


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_same_example_input_gives_the_same_result(path):
    assert _result(path.name) == _result(path.name)


@pytest.mark.parametrize("path", JUDGE_INPUTS, ids=lambda p: p.name)
def test_calling_again_with_the_returned_cursors_counts_no_new_words(path):
    inputs = _load(path.name)
    first = _result(path.name)
    again = {
        **inputs,
        "t_ms": inputs["t_ms"] + 1000,
        "since_ms": first.counted_until_ms,
        "words_since_ms": first.words_counted_until_ms,
    }
    [second] = judge(again, again["t_ms"], _config(path.name))
    pending_before = any(w.is_filler is None for w in first.words)
    if not pending_before:
        assert word_items(second) == []


def test_summarize_and_criteria_files_are_what_the_code_gives_now():
    assert summarize(_load("summarize_input.json")) == _load("summarize_output.json")
    assert {k: v.model_dump(mode="json") for k, v in criteria().items()} == _load("criteria.json")


def test_criteria_example_follows_the_issue_criteria_model():
    for value in _load("criteria.json").values():
        IssueCriteria.model_validate(value)


def test_example_files_come_in_input_output_pairs():
    for path in JUDGE_INPUTS:
        assert (EXAMPLES / _output_name(path.name)).exists()


# ── 기능 문서 5-5 와 같은 값 (세 층을 모두 켠 설정의 결과) ──────────────────────────────


def test_first_example_matches_the_documented_output():
    result = _result("judge_input.json")
    assert [(w.word, w.tier, w.is_filler, w.reason) for w in result.words] == [
        ("그니까", "T3", True, "앞뒤 간격이 기준 이상인 단독 단어"),
        ("지난", None, False, "사전에 없음"),
        ("음", "T1", True, "채움말"),
        ("분기", None, False, "사전에 없음"),
    ]
    assert (result.metrics.recent_filler_count, result.metrics.filler_per_min) == (2, 2.93)
    assert (result.counted_until_ms, result.words_counted_until_ms) == (41_000, 40_980)
    assert (result.measurable, result.state, result.issues) == (True, "NORMAL", [])
    assert [(i.t_ms, i.values) for i in result.tally] == [
        (40_050, {"filler_count": 1, "filler_t1": 1, "filler_word:음": 1}),
        (40_000, {"elapsed_ms": 1_000, "total_ms": 1_000}),
    ]


def test_pending_example_matches_the_documented_output():
    result = _result("judge_input_pending.json")
    assert [(w.word, w.tier, w.is_filler, w.reason) for w in result.words] == [
        ("매출은", None, False, "사전에 없음"),
        ("그", "T2", None, "뒤 간격을 아직 알 수 없음"),
    ]
    assert (result.metrics.recent_filler_count, result.metrics.filler_per_min) == (0, 0.0)
    assert (result.counted_until_ms, result.words_counted_until_ms) == (52_000, 50_800)
    assert [(i.t_ms, i.values) for i in result.tally] == [
        (51_000, {"elapsed_ms": 1_000, "total_ms": 1_000})
    ]


def test_take_end_example_decides_the_pending_word_as_a_filler():
    result = _result("judge_input_take_end.json")
    last = result.words[-1]
    assert (last.word, last.is_filler) == ("그", True)
    assert result.words_counted_until_ms == 51_650
    assert result.tally[0].values == {"filler_count": 1, "filler_t2": 1, "filler_word:그": 1}


def test_stt_down_example_is_unknown_and_counts_only_total_time():
    result = _result("judge_input_stt_down.json")
    assert (result.measurable, result.state, result.words, result.issues) == (
        False,
        "UNKNOWN",
        [],
        [],
    )
    assert result.words_counted_until_ms == 38_950
    assert [i.values for i in result.tally] == [{"total_ms": 1_000}]


def test_frequent_example_is_high_with_one_issue_from_mixed_tiers():
    result = _result("judge_input_frequent.json")
    assert (result.state, result.metrics.recent_filler_count) == ("HIGH", 8)
    assert len(result.issues) == 1
    tiers = {w.tier for w in result.words if w.is_filler}
    assert tiers == {"T1", "T2", "T3"}
    assert "같은 단어가 바로 반복" in {w.reason for w in result.words}


def test_summarize_example_matches_the_documented_values():
    out = summarize(_load("summarize_input.json"))["FILLER"]
    assert out == {
        "filler_count": 7,
        "filler_per_min": 2.4,
        "by_tier": {"T1": 4, "T2": 0, "T3": 3},
        "by_word": {"음": 4, "그니까": 2, "좀": 1},
        "measured_ratio": 0.97,
    }


def test_example_criteria_version_is_the_config_it_was_judged_with():
    assert _result("judge_input.json").criteria_version == criteria_version(ALL_TIERS)
    assert _result("judge_input_default.json").criteria_version == criteria_version(DEFAULT)


def test_default_example_does_not_count_the_t3_word_the_documented_example_counts():
    # 배포 기본값(T1만): 5-5 첫 예시의 "그니까"(T3)는 판정하지만 세지 않는다
    result = _result("judge_input_default.json")
    first = result.words[0]
    assert (first.word, first.tier, first.is_filler, first.reason) == (
        "그니까",
        "T3",
        False,
        "세지 않는 층",
    )
    assert result.metrics.recent_filler_count == 1
    assert [w.word for w in result.words if w.is_filler] == ["음"]
