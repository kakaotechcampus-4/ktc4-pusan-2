"""자연어 평가 기준 → 측정 가능 요소 추출 파이프라인: LLM 호출 (Structured Output)."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from .prompt import SYSTEM_PROMPT
from .schema import EvaluationCriteriaAnalysis

# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def build_llm() -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다.

    자유 키 dict 없이 고정 필드를 사용하여 json_schema 방식으로
    구조화된 출력을 생성합니다.
    (function tools가 아니라 response_format 기반이라 reasoning_effort와 충돌하지 않음)
    """
    load_dotenv(ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(f"OPENAI_API_KEY / OPENAI_MODEL이 비어 있습니다: {ENV_PATH}")

    llm = ChatOpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        model=model,
    )
    return llm.with_structured_output(EvaluationCriteriaAnalysis)


def analyze_criteria(text: str, llm: Runnable | None = None) -> dict:
    """자연어 평가 기준에서 측정 가능한 요소의 기준 값을 추출합니다.

    여러 입력을 돌릴 때는 build_llm()으로 만든 llm을 넘겨 재사용하세요.
    """
    llm = llm or build_llm()
    result = llm.invoke([("system", SYSTEM_PROMPT), ("user", text)])
    return result.model_dump()
