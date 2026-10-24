"""자연어 평가 기준 → 측정 가능 요소 추출 파이프라인: LLM 호출 (Structured Output)."""

from typing import Any

from .prompts import SYSTEM_PROMPT


def analyze_criteria(text: str, llm: Any) -> dict:
    """자연어 평가 기준에서 측정 가능한 요소의 기준 값을 추출합니다.

    llm은 EvaluationCriteriaAnalysis 출력 스키마가 걸린 객체(.invoke만 씀)입니다.
    여러 입력을 돌릴 때는 같은 llm을 넘겨 재사용하세요.
    """
    result = llm.invoke([("system", SYSTEM_PROMPT), ("user", text)])
    return result.model_dump()
