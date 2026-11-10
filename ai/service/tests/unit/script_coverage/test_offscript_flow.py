"""대본 밖 분류(LLM)를 켰을 때 `/takes/analyze` 흐름: 분류 결과 반영, 분류 실패는 장 채점을 실패로 하지 않는다."""

import re

import pytest

from pitch_coach_ai.features.script_coverage.core import TakeLLMs, evaluate_take
from pitch_coach_ai.features.script_coverage.stt_evaluation import core as stt_core
from pitch_coach_ai.features.script_coverage.stt_evaluation.offscript import (
    OffscriptItem,
    OffscriptJudgment,
)

from .fakes import KeepFirstVerifier
from .test_take_core import MODEL, NUMBERS, RUBRICS, SaidAllLLM, _script

ASIDE = "어제 본 드라마가 갑자기 생각나네요 주인공이 정말 멋있었습니다."


class AsideLLM:
    """입력의 대본 밖 구간 [O번호] 마다 무관한 말이라고 답하는 가짜 분류 LLM. `fail` 이면 예외."""

    def __init__(self, fail: BaseException | None = None):
        self.fail = fail
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        ids = re.findall(r"\[(O\d+)\]", messages[1][1])
        return OffscriptJudgment(
            items=[
                OffscriptItem(span_id=i, category="unrelated", reason="주제와 관계없음")
                for i in ids
            ]
        )


def _request() -> dict:
    stt = {n: _script(n) for n in NUMBERS}
    stt[NUMBERS[0]] = f"{stt[NUMBERS[0]]} {ASIDE}"
    return {
        "take_id": "take-123",
        "slides": [{"slide_number": n, "stt": stt[n]} for n in NUMBERS],
        "rubrics": [r.model_dump() for r in RUBRICS],
    }


def _analyze(offscript_llm):
    llms = TakeLLMs(eval=SaidAllLLM(), verifier=KeepFirstVerifier(), offscript=offscript_llm)
    return evaluate_take(_request(), llms=llms, model=MODEL)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(stt_core, "OFFSCRIPT_LLM_ENABLED", True)


def test_disabled_by_default_does_not_call_the_llm():
    llm = AsideLLM()
    response = _analyze(llm)
    assert llm.calls == 0
    first = response.evaluations[0]
    assert all(s.source == "rule" for s in first.offscript.spans)
    assert any(s.category == "off_script" for s in first.offscript.spans)


def test_enabled_applies_llm_categories(enabled):
    llm = AsideLLM()
    response = _analyze(llm)
    assert llm.calls >= 1
    first = response.evaluations[0]
    judged = [s for s in first.offscript.spans if s.source == "llm"]
    assert judged and all(s.category == "unrelated" for s in judged)
    assert first.detail.speech_mix.categories["unrelated"] > 0
    assert "OFFSCRIPT_DISABLED" not in first.offscript.reason_codes


@pytest.mark.parametrize(
    ("error", "reason"),
    [(TimeoutError("late"), "OFFSCRIPT_DEADLINE"), (RuntimeError("boom"), "OFFSCRIPT_FAILED")],
)
def test_classification_failure_keeps_the_slide_and_rule_values(enabled, error, reason):
    response = _analyze(AsideLLM(fail=error))
    assert response.failed == []  # 장 채점은 그대로 성공
    first = response.evaluations[0]
    assert reason in first.offscript.reason_codes
    assert reason in first.detail.speech_mix.reason_codes
    assert all(s.source == "rule" for s in first.offscript.spans)
