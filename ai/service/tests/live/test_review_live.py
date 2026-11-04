"""리뷰를 실제 LLM 으로 한 번 (과금, 호출 1번). `uv run python -m pytest -m live`.

`POST /takes/review` 를 서비스의 LLM 객체 · 역할(`review`) · 마감(25초)으로 부르고, 예시 요청
(`features/review/examples/review/request.json`)이 기본 결과가 아니라 LLM 문장으로 돌아오는지,
숫자 · 상태 · 점수는 LLM 이 없을 때와 같은지 본다(리뷰 기능 문서 6-2 · 11절).
`ai/.env` 의 기본 묶음을 직접 읽는다(`test_llm_live.py` 와 같다).
"""

import json
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from pitch_coach_ai.features.review import core, review_take
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live

REQUEST = json.loads(
    (Path(core.__file__).parent / "examples" / "review" / "request.json").read_text(
        encoding="utf-8"
    )
)
#: LLM 과 상관없이 규칙 단계가 정하는 값
RULE_FIELDS = ("score", "dimensions", "comparison", "mission_results", "criteria_results")


def _settings_from_env_file() -> Settings:
    env = {k.lower(): v for k, v in dotenv_values(AI_DIR / ".env").items() if v}
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(_env_file=None, **env)


def test_review_with_the_real_llm() -> None:
    with TestClient(create_app(_settings_from_env_file())) as client:
        started = time.monotonic()
        res = client.post("/takes/review", json=REQUEST)
        seconds = time.monotonic() - started
    assert res.status_code == 200, res.text
    body = res.json()
    print(
        f"model={body['meta']['model']} seconds={seconds:.1f} "
        f"summary_chars={len(body['overall_summary'])} next_missions={len(body['next_missions'])}"
    )
    assert body["fallback_reason"] is None
    assert body["meta"]["model"]
    assert seconds < 25
    rules = review_take(REQUEST).model_dump(mode="json")
    assert {k: body[k] for k in RULE_FIELDS} == {k: rules[k] for k in RULE_FIELDS}
