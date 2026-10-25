"""규칙 보정(수치를 말했으면 missing → vague)과 교차 검증 (#544).

수치 확인은 STT 전체에서 같은 값을 찾으므로, 같은 숫자가 다른 문맥에서 나와도 규칙이 단위를 vague 로
올린다. 교차 검증이 그 단위를 다시 missing 으로 판정하면, 그 수치가 나온 STT 문장이 대본 문장과 내용
형태소를 함께 가질 때만 보정을 유지하고 아니면 missing 으로 둔다.
"""

import re

from pitch_coach_ai.features.script_coverage.core import ScriptLLMs, analyze_script
from pitch_coach_ai.features.script_coverage.stt_evaluation.core import evaluate_take
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SemanticEvaluation,
    SentenceJudgment,
    SlideSTT,
    Take,
    UnitJudgment,
    VerifiedSentence,
    VerifierResult,
)

from ..fakes import DeckAwareLLM

SCRIPT = "우리 서비스는 효과가 컸습니다. 도입 뒤 매출이 35% 늘었습니다."
# 35% 는 나오지만 매출이 아니라 할인율 이야기다
STT = "우리 서비스는 효과가 컸습니다. 할인율은 35%입니다."


class _AllMissing:
    """1차 판정: 모든 문장의 모든 단위를 missing(확신 high)으로."""

    def invoke(self, messages):
        text = messages[1][1]
        return SemanticEvaluation(
            sentences=[
                SentenceJudgment(
                    sentence_id=int(i),
                    evidence=[],
                    reason="말하지 않았다",
                    units=[
                        UnitJudgment(unit_id=u, status="missing")
                        for u in re.findall(rf"^\s+- (S{i}-U\d+) ", text, flags=re.M)
                    ],
                    confidence="high",
                )
                for i in re.findall(r"^\[S(\d+)\]", text, flags=re.M)
            ],
            name_checks=[],
        )


class _VerifierSays:
    """교차 검증: 대상 문장의 모든 단위를 `status` 로 다시 판정한다."""

    def __init__(self, status: str):
        self.status = status
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        judgments: list[VerifiedSentence] = []
        for line in messages[1][1].splitlines():
            if m := re.match(r"## S(\d+) ", line):
                judgments.append(VerifiedSentence(sentence_id=int(m[1]), reason="검증", units=[]))
            elif m := re.match(r"  (S\d+-U\d+) ", line):
                judgments[-1].units.append(UnitJudgment(unit_id=m[1], status=self.status))
        return VerifierResult(judgments=judgments)


def _evaluate(verifier_status: str, stt: str = STT):
    llms = ScriptLLMs(**{s: DeckAwareLLM(s) for s in ("semantic", "final", "unit")})
    rubric = analyze_script(
        {"pitch_id": "p-1", "script_version_id": "sv-1", "slides": [{"slide_number": 1, "script": SCRIPT}]},
        llms=llms,
        model="test-model",
    ).rubrics[0]
    verifier = _VerifierSays(verifier_status)
    evaluations, _ = evaluate_take(
        Take(script_name="sv-1", take_id="t-1", slides=[SlideSTT(slide_number=1, stt=stt)]),
        {1: rubric},
        eval_llm=_AllMissing(),
        verifier_llm=verifier,
        model="test-model",
    )
    return evaluations[0].sentences[1], verifier


def test_number_in_another_context_raises_the_unit_by_rule_first() -> None:
    sentence, verifier = _evaluate("vague")
    assert verifier.calls == 1
    assert [(u.first_status, u.raised_by_rule) for u in sentence.units] == [("vague", True)]


def test_verifier_missing_undoes_the_rule_raise() -> None:
    # #544 재현: 고치기 전에는 검증자가 missing 이라 해도 vague · 문장 partial 로 남았다
    sentence, verifier = _evaluate("missing")
    assert verifier.calls == 1
    assert [(u.status, u.raised_by_rule) for u in sentence.units] == [("missing", True)]
    assert sentence.status == "missing"
    assert sentence.verified is True


def test_verifier_can_still_keep_the_unit_vague() -> None:
    sentence, _ = _evaluate("vague")
    assert [u.status for u in sentence.units] == ["vague"]
    assert sentence.status == "partial"


def test_rule_raise_survives_verifier_missing_when_the_number_is_said_in_context() -> None:
    # 요약해 말하며 같은 지표(매출)와 함께 수치를 말했다 — 검증자가 missing 이라 해도 vague 로 둔다
    stt = "우리 서비스는 효과가 컸습니다. 정리하면 매출이 35% 늘었습니다."
    sentence, verifier = _evaluate("missing", stt)
    assert verifier.calls == 1
    assert [(u.status, u.raised_by_rule) for u in sentence.units] == [("vague", True)]
    assert sentence.status == "partial"
