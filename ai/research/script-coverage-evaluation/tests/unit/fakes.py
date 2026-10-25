"""단위 테스트용 가짜 LLM: `.invoke(messages)` 가 정해진 스키마 객체를 돌려주거나 정해진 예외를 낸다."""

import re

from script_coverage.script_analysis.schemas import (
    FinalKeyPoint,
    FinalReview,
    KeyPointDraft,
    NameVerdict,
    SentenceLabel,
    SlideSemanticAnalysis,
    SlideUnits,
)
from script_coverage.stt_evaluation.schemas import (
    UnitJudgment,
    VerifiedSentence,
    VerifierResult,
)


class FakeLLM:
    """부른 횟수를 세고, `result` 를 돌려주거나 (예외 객체면) 그 예외를 낸다."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def empty_script_llms(semantic=None, final=None, unit=None) -> tuple[FakeLLM, FakeLLM, FakeLLM]:
    """아무것도 뽑지 않는 빈 응답을 내는 (1차 분석, 최종 결론, 전달 단위) 가짜 LLM. 인자로 바꿀 수 있다."""
    return (
        FakeLLM(semantic or SlideSemanticAnalysis(sentence_roles=[], core_claim="", key_points=[])),
        FakeLLM(
            final
            or FinalReview(
                sentence_roles=[], core_claim="", key_points=[], name_verdicts=[], changes=[]
            )
        ),
        FakeLLM(unit or SlideUnits(sentences=[])),
    )


class KeepFirstVerifier:
    """교차 검증 가짜: 검증 메시지의 대상 문장 · 전달 단위마다 1차 판정을 그대로 돌려준다.

    검증 출력은 대상을 빠짐없이 덮어야 하므로(check_verification) 빈 판정 대신 이것을 쓴다.
    """

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        judgments: list[VerifiedSentence] = []
        for line in messages[1][1].splitlines():
            if m := re.match(r"## S(\d+) ", line):
                judgments.append(
                    VerifiedSentence(sentence_id=int(m[1]), reason="1차 유지", units=[])
                )
            elif m := re.match(r"  (S\d+-U\d+) .* — (said|vague|missing|contradicted)$", line):
                judgments[-1].units.append(UnitJudgment(unit_id=m[1], status=m[2]))
        return VerifierResult(judgments=judgments)


class SlideFailingLLM(FakeLLM):
    """메시지의 `[슬라이드 N]` 로 장을 알아보고, `failing` 에 든 장만 예외를 낸다. 부른 장 번호를 남긴다."""

    def __init__(self, result, failing: dict[int, BaseException] | None = None):
        super().__init__(result)
        self.failing = failing or {}
        self.called_slides: list[int] = []

    def invoke(self, messages):
        self.calls += 1
        number = int(messages[1][1].split("]", 1)[0].removeprefix("[슬라이드 "))
        self.called_slides.append(number)
        if number in self.failing:
            raise self.failing[number]
        return self.result


class DeckAwareLLM:
    """메시지를 읽어 정해진 규칙으로 답하는 가짜 LLM (같은 메시지 → 같은 답)."""

    def __init__(self, stage):
        self.stage = stage
        self.messages: list[str] = []

    def invoke(self, messages):
        text = messages[1][1]
        self.messages.append(text)
        sentence_ids = [int(i) for i in re.findall(r"\[S(\d+)\]", text)]
        roles = [SentenceLabel(id=i, role="claim" if i == 0 else "evidence") for i in sentence_ids]
        if self.stage == "semantic":
            # 모든 장이 '도서관' 을 key_term 으로 올린다: 발표 공통 어휘인지는 다른 장을 봐야 안다
            key_points = [
                KeyPointDraft(content="도서관 이야기다.", sentence_ids=[0], key_terms=["도서관"])
            ]
            return SlideSemanticAnalysis(
                sentence_roles=roles, core_claim="핵심", key_points=key_points
            )
        if self.stage == "final":
            verdicts = [
                NameVerdict(candidate_id=c, keep=True, reason="테스트")
                for c in sorted(set(re.findall(r"\bN\d+\b", text)))
            ]
            return FinalReview(
                sentence_roles=roles,
                core_claim="핵심",
                key_points=[FinalKeyPoint(content="도서관 이야기다.", sentence_ids=[0])],
                name_verdicts=verdicts,
                changes=[],
            )
        return SlideUnits(sentences=[])


def deck_rubrics(numbers=(4, 6, 7), model="test-model") -> list:
    """가상대본1 의 평가 기준 (`DeckAwareLLM` 으로 만든 것). script_name 은 `sv-3`."""
    from script_coverage.core import ScriptLLMs, analyze_script

    from .shared.slides import SLIDES

    llms = ScriptLLMs(**{s: DeckAwareLLM(s) for s in ("semantic", "final", "unit")})
    response = analyze_script(
        {
            "pitch_id": "p-1",
            "script_version_id": "sv-3",
            "slides": [{"slide_number": n, "script": SLIDES[("가상대본1", n)]} for n in numbers],
        },
        llms=llms,
        model=model,
    )
    return response.rubrics
