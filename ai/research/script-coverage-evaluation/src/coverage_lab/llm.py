"""LLM 클라이언트 만들기. 코어는 클라이언트를 만들지 않으므로 research 가 여기서 만들어 넘긴다.

`.env` 의 키로 실제 API 를 부른다 (mock 모드는 없다). 이미 환경 변수에 있는 값은 `.env` 가 덮어쓰지 않는다.
"""

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from script_coverage.script_analysis.schemas import FinalReview, SlideSemanticAnalysis, SlideUnits
from script_coverage.stt_evaluation.schemas import SemanticEvaluation, VerifierResult

from .paths import PROJECT_ROOT


@dataclass(frozen=True)
class Settings:
    base_url: str | None
    api_key: str
    model: str


def find_env_file() -> str:
    """현재 디렉터리부터 상위로 올라가며 처음 만나는 .env. 없으면 프로젝트 폴더부터 올라가며 찾는다 ('' = 없음)."""
    found = find_dotenv(usecwd=True)
    if found:
        return found
    for folder in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (folder / ".env").is_file():
            return str(folder / ".env")
    return ""


def load_settings() -> Settings:
    """.env 를 읽어 (환경 변수에 없는 값만 채운다) 설정을 만든다. 키와 모델이 없으면 멈춘다."""
    env_path = find_env_file()
    load_dotenv(env_path, override=False)
    api_key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL")
    if not (api_key and model):
        raise RuntimeError(
            f".env 에 OPENAI_API_KEY / OPENAI_MODEL 이 없다 (읽은 파일: {env_path or '없음'})"
        )
    return Settings(base_url=os.getenv("OPENAI_BASE_URL"), api_key=api_key, model=model)


def structured_llm(schema: type[BaseModel], settings: Settings):
    """주어진 출력 스키마 모양으로만 답하도록 고정한 LLM (`.invoke(messages)` 가 스키마 객체를 돌려준다)."""
    client = ChatOpenAI(
        base_url=settings.base_url, api_key=settings.api_key, model=settings.model, max_retries=2
    )
    return client.with_structured_output(schema)


def script_llms(settings: Settings) -> dict:
    """`analyze_script(..., **script_llms(settings), model=settings.model)` 로 쓴다."""
    return {
        "semantic_llm": structured_llm(SlideSemanticAnalysis, settings),  # 1차 분석
        "final_llm": structured_llm(FinalReview, settings),  # 최종 결론
        "unit_llm": structured_llm(SlideUnits, settings),  # 전달 단위
    }


def stt_llms(settings: Settings) -> dict:
    """`evaluate_take(..., **stt_llms(settings), model=settings.model)` 로 쓴다."""
    return {
        "eval_llm": structured_llm(SemanticEvaluation, settings),  # 의미 평가
        "verifier_llm": structured_llm(VerifierResult, settings),  # 교차 검증
    }
