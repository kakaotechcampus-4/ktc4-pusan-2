"""run.py --eval 의 채점 (evaluation-criteria.md 13 · 14절). 가짜 LLM 으로 본다."""

from types import SimpleNamespace

import pytest

from run import MeasuredLLM, evaluate, score_case, summarize
from tests.fakes import EMPTY_VALUES, analysis


def expected(values=None, targets=()):
    return {"values": {**EMPTY_VALUES, **(values or {})}, "targets": list(targets), "excluded": []}


def target(metric="filler_count", operator="LTE", value=5, display="d"):
    return {"display": display, "metric": metric, "operator": operator, "value": value}


def output(values=None, targets=()):
    return {"display_criteria": ["d"], "values": {**EMPTY_VALUES, **(values or {})},
            "targets": list(targets), "excluded": []}  # fmt: skip


# ── score_case ────────────────────────────────────────────────────────────────


def test_same_answer_scores_everything():
    exp = expected({"filler": "5번 이하", "script_used": True}, [target()])
    score = score_case(exp, output({"filler": "5회 이하", "script_used": True}, [target(value=5.0)]))
    assert score == {"elements_correct": 9, "element_errors": [], "missing_targets": [],
                     "fabricated_targets": []}  # fmt: skip


def test_element_compares_only_null_or_not():
    # 값 문구가 달라도 둘 다 null 이 아니면 맞다
    score = score_case(expected({"gaze": "청중을 자주 보기"}), output({"gaze": "청중 응시"}))
    assert score["elements_correct"] == 9


def test_element_null_mismatch_is_an_error():
    score = score_case(expected({"pause": "5번 이하"}), output({"silence": "5번 이하"}))
    assert score["elements_correct"] == 7
    assert len(score["element_errors"]) == 2


@pytest.mark.parametrize("got", [False, None])
def test_script_used_compares_the_value(got):
    score = score_case(expected({"script_used": True}), output({"script_used": got}))
    assert score["elements_correct"] == 8


def test_target_needs_metric_operator_and_value_to_match():
    exp = expected({"filler": "x"}, [target()])
    out = output({"filler": "x"}, [target(operator="LT")])
    score = score_case(exp, out)
    assert score["missing_targets"] == [("filler_count", "LTE", 5.0)]
    assert score["fabricated_targets"] == [("filler_count", "LT", 5.0)]


def test_display_is_not_compared():
    score = score_case(expected(targets=[target(display="a")]), output(targets=[target(display="b")]))
    assert score["missing_targets"] == score["fabricated_targets"] == []


def test_failed_call_counts_as_all_wrong():
    score = score_case(expected(targets=[target()]), None)
    assert score["elements_correct"] == 0
    assert score["missing_targets"] == [("filler_count", "LTE", 5.0)]
    assert score["fabricated_targets"] == []


# ── summarize ─────────────────────────────────────────────────────────────────


def case(exp, out, error=None):
    return {"expected": exp, "score": score_case(exp, out), "error": error}


def test_summary_metrics():
    cases = [
        case(expected(targets=[target(), target("cpm", "LTE", 300)]), output(targets=[target()])),
        case(expected(), output(targets=[target("pause_count", "LTE", 3)])),
    ]
    metrics = summarize(cases)
    assert metrics["target_recall"] == 0.5  # 정답 2개 중 1개
    assert metrics["target_precision"] == 0.5  # 나온 2개 중 1개
    assert metrics["fabricated_targets_per_sentence"] == 0.5  # 지어낸 1개 ÷ 2문장
    assert metrics["element_accuracy"] == 1.0
    assert metrics["display_user_wording_ratio"] is None  # 사람이 채운다
    assert metrics["failed"] == 0


def test_no_targets_anywhere_gives_none_not_zero_division():
    metrics = summarize([case(expected(), output())])
    assert metrics["target_recall"] is None and metrics["target_precision"] is None


# ── MeasuredLLM · evaluate ────────────────────────────────────────────────────


class FakeRawLLM:
    """build_llm(include_raw=True) 흉내: {raw, parsed, parsing_error} 를 돌려준다."""

    def __init__(self, parsed=None, parsing_error=None, fail=None):
        self.parsed = parsed if parsed is not None else analysis()
        self.parsing_error = parsing_error
        self.fail = fail

    def invoke(self, messages):
        if self.fail:
            raise self.fail
        raw = SimpleNamespace(
            usage_metadata={"output_tokens": 120, "output_token_details": {"reasoning": 64}},
            response_metadata={"model_name": "fake-model"},
        )
        return {"raw": raw, "parsed": self.parsed, "parsing_error": self.parsing_error}


def test_measured_llm_records_tokens_and_model():
    llm = MeasuredLLM(FakeRawLLM())
    assert llm.invoke([("user", "x")]) == analysis()
    [call] = llm.calls
    assert call["output_tokens"] == 120 and call["reasoning_tokens"] == 64
    assert call["model"] == "fake-model" and call["latency_s"] >= 0


def test_measured_llm_raises_parsing_error():
    with pytest.raises(ValueError):
        MeasuredLLM(FakeRawLLM(parsing_error=ValueError("bad"))).invoke([("user", "x")])


EXAMPLE = {"case": "추임새", "text": "추임새는 5번 이하로 해 주세요.",
           "expected": expected({"filler": "5번 이하"}, [target()])}  # fmt: skip


def test_evaluate_report():
    parsed = analysis(["d"], {"filler": "5번 이하"}, [target()])
    report = evaluate([EXAMPLE], MeasuredLLM(FakeRawLLM(parsed)))
    assert report["metrics"]["target_recall"] == 1.0
    assert report["meta"]["models"] == ["fake-model"]
    assert report["measurements"]["output_tokens"] == {"min": 120, "median": 120, "max": 120}
    [c] = report["cases"]
    assert c["error"] is None and c["display_user_wording"] is None
    assert "meta" not in c["output"]


def test_evaluate_keeps_going_when_a_call_fails():
    report = evaluate([EXAMPLE, EXAMPLE], MeasuredLLM(FakeRawLLM(fail=TimeoutError("late"))))
    assert report["metrics"]["failed"] == 2
    assert all(c["output"] is None and "TimeoutError" in c["error"] for c in report["cases"])
