"""가짜 LLM — analyze_criteria 가 받는 `.invoke` 만 흉내 낸다 (architecture.md 9절 '테스트')."""

from typing import Any

from evaluation_criteria.schemas import EvaluationCriteriaAnalysis

ELEMENTS = [
    "speed",
    "pause",
    "silence",
    "volume",
    "filler",
    "gaze",
    "script_dependency",
    "script_similarity",
    "script_used",
]
EMPTY_VALUES = dict.fromkeys(ELEMENTS)


def analysis(
    display_criteria: list[str] | None = None,
    values: dict[str, Any] | None = None,
    targets: list[dict[str, Any]] | None = None,
    excluded: list[str] | None = None,
) -> EvaluationCriteriaAnalysis:
    """LLM 출력 하나. 주지 않은 필드는 빈 값이다."""
    return EvaluationCriteriaAnalysis.model_validate(
        {
            "display_criteria": display_criteria or [],
            "values": {**EMPTY_VALUES, **(values or {})},
            "targets": targets or [],
            "excluded": excluded or [],
        }
    )


class FakeLLM:
    """정해진 출력을 돌려주고, 받은 메시지를 calls 에 남긴다. error 를 주면 그 예외를 낸다."""

    def __init__(
        self, output: EvaluationCriteriaAnalysis | None = None, error: Exception | None = None
    ):
        self.output = output if output is not None else analysis()
        self.error = error
        self.calls: list[list[tuple[str, str]]] = []

    def invoke(self, messages: list[tuple[str, str]]) -> EvaluationCriteriaAnalysis:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.output
