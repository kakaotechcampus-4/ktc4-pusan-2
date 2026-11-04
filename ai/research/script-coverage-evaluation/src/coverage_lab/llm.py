"""LLM 클라이언트 만들기. 코어는 클라이언트를 만들지 않으므로 research 가 여기서 만들어 넘긴다.

`.env` 의 키로 실제 API 를 부른다 (mock 모드는 없다). 이미 환경 변수에 있는 값은 `.env` 가 덮어쓰지 않는다.
"""

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from pitch_coach_ai.features.script_coverage.core import ScriptLLMs, TakeLLMs
from pitch_coach_ai.features.script_coverage.script_analysis.schemas import (
    FinalReview,
    SlideSemanticAnalysis,
    SlideUnits,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SemanticEvaluation,
    VerifierResult,
)

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


def structured_llm(
    schema: type[BaseModel],
    settings: Settings,
    *,
    max_retries: int = 2,
    timeout: float | None = None,
    streaming: bool = False,
    max_completion_tokens: int | None = None,
    callbacks: list | None = None,
):
    """주어진 출력 스키마 모양으로만 답하도록 고정한 LLM (`.invoke(messages)` 가 스키마 객체를 돌려준다).

    - `max_retries`: SDK 가 알아서 다시 부르는 횟수. 재시도를 직접 세어야 하는 시간 측정은 0 으로 준다
    - `timeout` · `streaming`: 시간 측정은 서비스 `common/llm.py` 처럼 전송 시간 제한을 호출 시간 제한과 같게 두고
      (버린 호출이 오래 남지 않게) 스트리밍으로 받는다. 연구 실험의 기본값은 예전 그대로다
    - `max_completion_tokens`: 출력 토큰 상한(추론 토큰 포함). 서비스의 역할별 상한과 같은 조건으로 잴 때 준다
    - `callbacks`: 호출마다 출력 토큰 수를 모을 때 `coverage_lab.usage.UsageLog` 를 넘긴다
    """
    client = ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        max_retries=max_retries,
        timeout=timeout,
        streaming=streaming,
        stream_usage=streaming,
        max_completion_tokens=max_completion_tokens,
        callbacks=callbacks,
    )
    return client.with_structured_output(schema)


def script_llms(settings: Settings) -> ScriptLLMs:
    """`/scripts/rubric` 진입 함수(`script_coverage.core.analyze_script`)에 넘기는 LLM 묶음."""
    return ScriptLLMs(
        semantic=structured_llm(SlideSemanticAnalysis, settings),  # 1차 분석
        final=structured_llm(FinalReview, settings),  # 최종 결론
        unit=structured_llm(SlideUnits, settings),  # 전달 단위
    )


def stt_llms(settings: Settings) -> TakeLLMs:
    """`/takes/analyze` 진입 함수(`script_coverage.core.evaluate_take`)에 넘기는 LLM 묶음."""
    return TakeLLMs(
        eval=structured_llm(SemanticEvaluation, settings),  # 의미 평가
        verifier=structured_llm(VerifierResult, settings),  # 교차 검증
    )
