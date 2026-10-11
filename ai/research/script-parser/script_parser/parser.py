"""대본 → 슬라이드 분리 파이프라인: LLM 호출 → clean_script → highlight."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from .postprocess import clean_script, find_highlights
from .prompt import SYSTEM_PROMPT
from .schema import SeparatedSlides

# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def build_llm() -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다."""
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
    return llm.with_structured_output(SeparatedSlides)


def parse_script(text: str, llm: Runnable | None = None) -> dict:
    """발표 대본 전체를 슬라이드별로 분리합니다.

    여러 대본을 돌릴 때는 build_llm()으로 만든 llm을 넘겨 재사용하세요.
    """
    llm = llm or build_llm()
    result = llm.invoke([("system", SYSTEM_PROMPT), ("user", text)])
    data = result.model_dump()

    # highlight 오프셋은 정제된 script 기준이라 clean_script가 먼저
    for slide in data["slides"]:
        slide["script"] = clean_script(slide["script"])
        slide["highlights"] = find_highlights(slide["script"], slide["keywords"])
    return data
