"""실제 LLM 을 1번 부른다 (과금). 기본 실행에서 빠지고 `uv run python -m pytest -m live` 로만 돈다.

- `ai/.env` 의 기본 묶음(OPENAI_*)을 직접 읽는다. conftest 가 단위 테스트용으로 묶음 환경변수를
  비워 두므로, 여기서는 파일 값을 Settings 에 직접 넘긴다(직접 넘긴 값이 가장 앞선다)
- 확인: 모델이 스키마대로 답하는지, 응답한 모델 이름을 받는지, 시간 제한 안에 끝나는지
"""

import time

import pytest
from dotenv import dotenv_values
from pydantic import BaseModel, Field

from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.common.meta import build_meta
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live


class Sum(BaseModel):
    answer: int = Field(description="계산 결과 숫자")


def _settings_from_env_file() -> Settings:
    env = {k.lower(): v for k, v in dotenv_values(AI_DIR / ".env").items() if v}
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(_env_file=None, **env)


def test_real_llm_answers_in_schema_and_reports_model() -> None:
    s = _settings_from_env_file()
    llm = structured_llm(Sum, deadline=deadline_after(25), app_settings=s)
    started = time.monotonic()
    result = llm.invoke("1 더하기 1은? answer 에 숫자로만 답하세요.")
    seconds = time.monotonic() - started
    print(f"model={llm.responded_model} seconds={seconds:.1f}")
    assert result == Sum(answer=2)
    assert llm.responded_model
    assert seconds < s.llm_timeout_seconds * (s.llm_max_retries + 1)
    assert build_meta("live-0", llm=llm).model == llm.responded_model
