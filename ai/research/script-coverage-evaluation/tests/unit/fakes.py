"""단위 테스트용 가짜 LLM: `.invoke(messages)` 가 정해진 스키마 객체를 돌려주거나 정해진 예외를 낸다."""

from script_coverage.script_analysis.schemas import (
    FinalReview,
    SlideSemanticAnalysis,
    SlideUnits,
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
