"""LLM 클라이언트 만들기. 코어는 클라이언트를 만들지 않으므로 research 가 여기서 만들어 넘긴다.

`.env` 의 키로 실제 API 를 부른다 (mock 모드는 없다). 이미 환경 변수에 있는 값은 `.env` 가
덮어쓰지 않는다. 코치(coach_lab/llm.py)와 같은 변수를 쓴다:
OPENAI_API_KEY · OPENAI_MODEL · OPENAI_BASE_URL.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI

from review.prompts.review import REVIEW_SYSTEM_PROMPT
from review.schemas import ReviewDraft

from .paths import PROJECT_ROOT

#: 호출 한 번의 시간 제한 · 재시도 (architecture.md 9절 기본값과 같게)
TIMEOUT_S = 12.0
MAX_RETRIES = 1


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


def review_llm(settings: Settings) -> Any:
    """ReviewDraft 모양으로만 답하도록 고정한 LLM. `review_take(..., llm=..., model=s.model)`"""
    client = ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=TIMEOUT_S,
        max_retries=MAX_RETRIES,
    )
    return client.with_structured_output(ReviewDraft)


class CachedLLM:
    """실제 LLM 앞의 캐시 겸 기록기.

    같은 프롬프트 · 출력 스키마 · 모델 · 입력이면 캐시(JSON 파일)의 답을 돌려줘 다시 부르지 않는다.
    마지막 호출의 입력 메시지 · LLM 초안(숫자 검사 전) · 실제로 부른 시간을 남긴다.
    LLM 예외는 그대로 올린다 (review_take 가 잡아 기본 결과로 돌아간다).
    """

    def __init__(self, llm: Any, model: str, cache_path: Path) -> None:
        self.llm = llm
        self.cache_path = cache_path
        self.cache: dict[str, Any] = (
            json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        )
        schema = json.dumps(ReviewDraft.model_json_schema(), sort_keys=True)
        self.prefix = f"{model}\n{REVIEW_SYSTEM_PROMPT}\n{schema}\n"
        self.last_message: str | None = None
        self.last_draft: ReviewDraft | None = None
        self.last_latency_ms: float | None = None  # 캐시에서 꺼냈으면 None
        self.calls = 0

    def invoke(self, messages: list[tuple[str, str]]) -> ReviewDraft:
        self.last_message = messages[-1][1]
        self.last_draft = self.last_latency_ms = None
        key = hashlib.sha256((self.prefix + self.last_message).encode("utf-8")).hexdigest()
        if key in self.cache:
            draft = ReviewDraft.model_validate(self.cache[key])
        else:
            started = time.perf_counter()
            out = self.llm.invoke(messages)
            self.last_latency_ms = (time.perf_counter() - started) * 1000
            self.calls += 1
            draft = out if isinstance(out, ReviewDraft) else ReviewDraft.model_validate(out)
            self.cache[key] = draft.model_dump(mode="json")
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        self.last_draft = draft
        return draft
