"""LLM 클라이언트 만들기. 코어는 클라이언트를 만들지 않으므로 research 가 여기서 만들어 넘긴다.

`.env` 의 키로 실제 API 를 부른다 (mock 모드는 없다). 이미 환경 변수에 있는 값은 `.env` 가
덮어쓰지 않는다. 대본 전달도(coverage_lab/llm.py)와 같은 변수를 쓴다:
OPENAI_API_KEY · OPENAI_MODEL · OPENAI_BASE_URL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI

from coach.schemas import PlanDraft

from .paths import PROJECT_ROOT


@dataclass(frozen=True)
class Settings:
    base_url: str | None
    api_key: str
    model: str


def find_env_file() -> str:
    """현재 폴더부터 위로 올라가며 처음 만나는 .env. 없으면 프로젝트 폴더부터 올라가며 찾는다."""
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


def plan_llm(settings: Settings, timeout_s: float = 20.0):
    """PlanDraft 모양으로만 답하도록 고정한 LLM.

    `plan_coaching(..., llm=plan_llm(s), model=s.model)` 로 넘긴다. 재시도하지 않는다 —
    Take 시작을 오래 붙잡느니 계획 없이 시작하는 편이 낫다. timeout 은 요청의 연결 · 읽기
    제한이라 전체 시간을 보장하지 않으므로, 전체 상한은 부르는 쪽 타임아웃이 맡는다.
    """
    client = ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=timeout_s,
        max_retries=0,
    )
    return client.with_structured_output(PlanDraft)
