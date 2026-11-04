"""가짜 LLM 출력 도우미 — test_eval.py 가 쓴다.

단위 테스트는 서비스(`ai/service/tests/unit/evaluation_criteria/`)로 옮겼다. 이 파일은 그 도우미
(`fakes.py`) 중 `EMPTY_VALUES` · `analysis` 의 사본이다.
"""

from typing import Any

from pitch_coach_ai.features.evaluation_criteria.schemas import EvaluationCriteriaAnalysis

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
