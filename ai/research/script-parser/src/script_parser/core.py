"""대본 → 슬라이드 분리 파이프라인: LLM 호출 → clean_script → highlight."""

from langchain_core.runnables import Runnable

from .cleaner import clean_script, find_highlights
from .prompts.parse import SYSTEM_PROMPT


def parse_script(text: str, llm: Runnable) -> dict:
    """발표 대본 전체를 슬라이드별로 분리합니다.

    llm은 SeparatedSlides 스키마가 걸린 Structured Output LLM입니다(run.py의 build_llm()).
    """
    result = llm.invoke([("system", SYSTEM_PROMPT), ("user", text)])
    data = result.model_dump()

    # highlight 오프셋은 정제된 script 기준이라 clean_script가 먼저
    for slide in data["slides"]:
        slide["script"] = clean_script(slide["script"])
        slide["highlights"] = find_highlights(slide["script"], slide["keywords"])
    return data
